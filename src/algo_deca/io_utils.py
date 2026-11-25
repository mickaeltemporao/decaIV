"""Configuration loading, tabular file reading (CSV/XLSX), and input validation."""

from __future__ import annotations

import csv
import hashlib
import tomllib
from pathlib import Path

import openpyxl
from pydantic import ValidationError

from algo_deca.models import AppConfig, ClassInfo, DataValidationError, StudentPreference


def sha256_file(path: Path) -> str:
    """Compute the SHA-256 hex digest of a file."""
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def load_config(config_path: Path) -> AppConfig:
    """Load and validate TOML configuration, resolving relative paths against config_path."""
    if not config_path.exists():
        raise DataValidationError([f"Config file not found: {config_path}"])
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
        config = AppConfig.model_validate(raw)
    except (tomllib.TOMLDecodeError, ValidationError, ValueError) as exc:
        raise DataValidationError([f"Invalid configuration in {config_path}: {exc}"]) from exc

    base_dir = config_path.resolve().parent
    for field_name in ("students_file", "classes_file", "output_dir"):
        raw_path = Path(getattr(config.paths, field_name))
        if not raw_path.is_absolute():
            setattr(config.paths, field_name, str((base_dir / raw_path).resolve()))
    return config


def _format_cell(value: object) -> str:
    """Convert a CSV or Excel cell value to a clean, stripped string."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return str(value).strip()
    return str(value).strip()


def _read_tabular(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Read a .csv or .xlsx file into (headers, list of row dicts)."""
    if not path.exists():
        raise DataValidationError([f"Input file not found: {path}"])

    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames is None:
                raise DataValidationError([f"CSV file is empty or missing headers: {path}"])
            headers = [h.strip() for h in reader.fieldnames]
            rows: list[dict[str, str]] = []
            for raw_row in reader:
                cleaned = {
                    (k.strip() if k is not None else ""): _format_cell(v)
                    for k, v in raw_row.items()
                }
                if any(val != "" for val in cleaned.values()):
                    rows.append(cleaned)
            return headers, rows

    if suffix == ".xlsx":
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            ws = wb.active
            if ws is None:
                raise DataValidationError([f"Excel workbook has no active sheet: {path}"])
            row_iter = ws.iter_rows(values_only=True)
            try:
                raw_headers = next(row_iter)
            except StopIteration as exc:
                raise DataValidationError([f"Excel file is empty: {path}"]) from exc
            headers = [_format_cell(h) for h in raw_headers]
            if not any(headers):
                raise DataValidationError([f"Excel header row is empty: {path}"])
            rows = []
            for raw_row in row_iter:
                row_dict = {
                    headers[idx]: _format_cell(raw_row[idx] if idx < len(raw_row) else None)
                    for idx in range(len(headers))
                    if headers[idx] != ""
                }
                if any(val != "" for val in row_dict.values()):
                    rows.append(row_dict)
            return [h for h in headers if h != ""], rows
        finally:
            wb.close()

    raise DataValidationError(
        [f"Unsupported file format '{path.suffix}' for {path}. Use .csv or .xlsx."]
    )


def _parse_int(value: str, field_name: str, row_num: int, errors: list[str]) -> int | None:
    """Parse a non-negative integer string, recording an error if invalid."""
    if value == "":
        errors.append(f"Classes row {row_num}: '{field_name}' is empty.")
        return None
    try:
        parsed = int(value)
    except ValueError:
        errors.append(f"Classes row {row_num}: '{field_name}' must be an integer, got '{value}'.")
        return None
    if parsed < 0:
        errors.append(f"Classes row {row_num}: '{field_name}' must be >= 0, got {parsed}.")
        return None
    return parsed


def load_and_validate_inputs(
    students_path: Path,
    classes_path: Path,
    config: AppConfig,
) -> tuple[list[StudentPreference], list[ClassInfo], list[str]]:
    """Load, cross-validate, and return (students, classes, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []

    class_headers, class_rows = _read_tabular(classes_path)
    student_headers, student_rows = _read_tabular(students_path)

    cols = config.columns
    opt = config.optimization
    required_class_cols = [cols.class_id]
    if opt.default_max_capacity is None:
        required_class_cols.append(cols.max_capacity)
    missing_class_cols = [c for c in required_class_cols if c not in class_headers]
    if missing_class_cols:
        errors.append(
            f"Classes file missing required column(s): {', '.join(missing_class_cols)}"
        )

    has_max_capacity = cols.max_capacity in class_headers
    has_min_capacity = cols.min_capacity in class_headers
    has_time_slot = cols.time_slot in class_headers
    has_eligible_classes = cols.eligible_classes in student_headers
    has_unavailable_slots = cols.unavailable_slots in student_headers

    required_student_cols = [cols.student_id, *cols.choice_columns]
    missing_student_cols = [c for c in required_student_cols if c not in student_headers]
    if missing_student_cols:
        errors.append(
            f"Students file missing required column(s): {', '.join(missing_student_cols)}"
        )

    if errors:
        raise DataValidationError(errors)

    classes: list[ClassInfo] = []
    seen_class_ids: set[str] = set()
    class_time_slots: dict[str, str] = {}
    for idx, row in enumerate(class_rows, start=2):
        cid = row.get(cols.class_id, "")
        if not cid:
            errors.append(f"Classes row {idx}: '{cols.class_id}' is empty.")
            continue
        if cid in seen_class_ids:
            errors.append(f"Classes row {idx}: duplicate class_id '{cid}'.")
            continue
        seen_class_ids.add(cid)

        raw_max = row.get(cols.max_capacity, "") if has_max_capacity else ""
        if raw_max != "":
            max_cap = _parse_int(raw_max, cols.max_capacity, idx, errors)
        elif opt.default_max_capacity is not None:
            max_cap = opt.default_max_capacity
        else:
            errors.append(f"Classes row {idx}: '{cols.max_capacity}' is empty.")
            max_cap = None

        raw_min = row.get(cols.min_capacity, "") if has_min_capacity else ""
        if raw_min != "":
            min_cap = _parse_int(raw_min, cols.min_capacity, idx, errors)
        else:
            min_cap = opt.default_min_capacity

        if max_cap is None or min_cap is None:
            continue
        if min_cap > max_cap:
            errors.append(
                f"Classes row {idx} ('{cid}'): min_capacity ({min_cap}) > max_capacity ({max_cap})."
            )
            continue

        t_slot = row.get(cols.time_slot, "") if has_time_slot else ""
        if t_slot:
            class_time_slots[cid] = t_slot
        classes.append(
            ClassInfo(
                class_id=cid,
                max_capacity=max_cap,
                min_capacity=min_cap,
                time_slot=t_slot or None,
            )
        )

    if not classes and not errors:
        errors.append("Classes file contains no class records.")

    students: list[StudentPreference] = []
    seen_student_ids: set[str] = set()
    mentioned_classes: set[str] = set()
    mention_counts: dict[str, int] = {c.class_id: 0 for c in classes}
    partial_rank_count = 0

    for idx, row in enumerate(student_rows, start=2):
        sid = row.get(cols.student_id, "")
        if not sid:
            errors.append(f"Students row {idx}: '{cols.student_id}' is empty.")
            continue
        if sid in seen_student_ids:
            errors.append(f"Students row {idx}: duplicate student_id '{sid}'.")
            continue
        seen_student_ids.add(sid)

        eligible_set: set[str] | None = None
        if has_eligible_classes:
            raw_elig = row.get(cols.eligible_classes, "")
            if raw_elig != "":
                elig_items = [item.strip() for item in raw_elig.split(";") if item.strip()]
                unknown_elig = [item for item in elig_items if item not in seen_class_ids]
                if unknown_elig:
                    errors.append(
                        f"Students row {idx} ('{sid}'): unknown class(es) in "
                        f"'{cols.eligible_classes}': {', '.join(unknown_elig)}."
                    )
                else:
                    eligible_set = set(elig_items)

        if has_unavailable_slots:
            raw_unavail = row.get(cols.unavailable_slots, "")
            if raw_unavail != "":
                unavail_slots = {item.strip() for item in raw_unavail.split(";") if item.strip()}
                if unavail_slots and class_time_slots:
                    base_pool = eligible_set if eligible_set is not None else set(seen_class_ids)
                    eligible_set = {
                        cid
                        for cid in base_pool
                        if class_time_slots.get(cid) not in unavail_slots
                    }

        choices: list[str] = []
        seen_choices: set[str] = set()
        for col_name in cols.choice_columns:
            choice = row.get(col_name, "")
            if choice == "":
                continue
            if choice in seen_choices:
                errors.append(
                    f"Students row {idx} ('{sid}'): duplicate class '{choice}' in preferences."
                )
                continue
            if seen_class_ids and choice not in seen_class_ids:
                errors.append(
                    f"Students row {idx} ('{sid}'): unknown class '{choice}' in '{col_name}'."
                )
                continue
            if eligible_set is not None and choice not in eligible_set:
                errors.append(
                    f"Students row {idx} ('{sid}'): ranked class '{choice}' in '{col_name}' "
                    "conflicts with student's schedule eligibility."
                )
                continue
            seen_choices.add(choice)
            choices.append(choice)
            mentioned_classes.add(choice)
            if choice in mention_counts:
                mention_counts[choice] += 1

        if not choices:
            errors.append(f"Students row {idx} ('{sid}'): student has no ranked choices.")
            continue

        if len(choices) < len(cols.choice_columns):
            partial_rank_count += 1

        students.append(
            StudentPreference(
                student_id=sid,
                choices=choices,
                eligible_classes=sorted(eligible_set) if eligible_set is not None else None,
            )
        )

    if not students and not errors:
        errors.append("Students file contains no student records.")

    if classes and students:
        total_max = sum(c.max_capacity for c in classes)
        total_min = sum(c.min_capacity for c in classes)
        if total_max < len(students):
            errors.append(
                f"Insufficient total class capacity: {total_max} seats for "
                f"{len(students)} students."
            )
        if total_min > len(students):
            errors.append(
                f"Infeasible minimum capacities: sum(min_capacity)={total_min} > "
                f"{len(students)} students."
            )

    if errors:
        raise DataValidationError(errors)

    if partial_rank_count > 0:
        warnings.append(
            f"{partial_rank_count} student(s) ranked fewer than "
            f"{len(cols.choice_columns)} choices."
        )

    unmentioned = sorted(seen_class_ids - mentioned_classes)
    if unmentioned:
        warnings.append(
            f"{len(unmentioned)} class(es) received zero preference mentions: "
            f"{', '.join(unmentioned[:5])}"
            + ("..." if len(unmentioned) > 5 else "")
        )

    under_min_mentions = [
        f"{c.class_id} ({mention_counts.get(c.class_id, 0)} mentions < min {c.min_capacity})"
        for c in classes
        if c.min_capacity > 0 and mention_counts.get(c.class_id, 0) < c.min_capacity
    ]
    if under_min_mentions:
        warnings.append(
            "Some classes have fewer preference mentions than min_capacity; unranked eligible "
            "students may be needed to satisfy minimums (or allow_unranked=false will be "
            f"infeasible): {', '.join(under_min_mentions[:5])}"
            + ("..." if len(under_min_mentions) > 5 else "")
        )

    weights = config.optimization.rank_weights
    if len(weights) >= 2 and config.optimization.tie_break_epsilon > 0:
        min_gap = min(
            weights[i] - weights[i + 1]
            for i in range(len(weights) - 1)
            if weights[i] > weights[i + 1]
        )
        max_tie_sum = len(students) * config.optimization.tie_break_epsilon
        if min_gap > 0 and max_tie_sum >= 0.1 * min_gap:
            warnings.append(
                f"tie_break_epsilon ({config.optimization.tie_break_epsilon}) * {len(students)} "
                f"students = {max_tie_sum:.4f}, which is >= 10% of smallest rank weight gap "
                f"({min_gap}). Consider reducing tie_break_epsilon."
            )

    return students, classes, warnings
