"""Generate realistic sample student preference and class capacity datasets."""

from __future__ import annotations

import csv
import math
import random
from pathlib import Path

import openpyxl


def _write_table(path: Path, headers: list[str], rows: list[list[object]]) -> None:
    """Write tabular data to .csv or .xlsx based on file extension."""
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(headers)
            writer.writerows(rows)
        return
    if suffix == ".xlsx":
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "data"
        ws.append(headers)
        for row in rows:
            ws.append(row)
        wb.save(path)
        return
    raise ValueError(f"Unsupported output format '{path.suffix}'. Use .csv or .xlsx.")


def generate_sample_dataset(
    students_out: Path,
    classes_out: Path,
    num_students: int = 300,
    num_classes: int = 15,
    num_choices: int = 5,
    max_capacity: int | None = None,
    min_capacity: int | None = None,
    seed: int = 2026,
) -> tuple[Path, Path]:
    """Generate a realistic synthetic dataset with oversubscribed popular classes."""
    if num_classes < num_choices:
        raise ValueError(
            f"num_classes ({num_classes}) must be >= num_choices ({num_choices})."
        )
    if num_students < 1:
        raise ValueError("num_students must be >= 1.")

    resolved_max = (
        max_capacity
        if max_capacity is not None
        else math.ceil(num_students * 1.05 / num_classes)
    )
    resolved_min = (
        min_capacity
        if min_capacity is not None
        else min(resolved_max, max(0, round(num_students / num_classes * 0.5)))
    )

    if resolved_max < 0 or resolved_min < 0 or resolved_min > resolved_max:
        raise ValueError(
            f"Invalid capacities: min_capacity={resolved_min}, max_capacity={resolved_max}."
        )
    if resolved_max * num_classes < num_students:
        raise ValueError(
            f"Insufficient total capacity: {resolved_max} * {num_classes} = "
            f"{resolved_max * num_classes} < {num_students} students."
        )
    if resolved_min * num_classes > num_students:
        raise ValueError(
            f"Infeasible minimum capacity: {resolved_min} * {num_classes} = "
            f"{resolved_min * num_classes} > {num_students} students."
        )

    rng = random.Random(seed)
    class_ids = [f"CLS-{i:02d}" for i in range(1, num_classes + 1)]

    # Skewed popularity weights so the top 3-4 classes are heavily oversubscribed on 1st choice.
    popularity_weights = [1.0 / ((i + 1) ** 0.85) for i in range(num_classes)]

    class_rows: list[list[object]] = [
        [cid, resolved_max, resolved_min] for cid in class_ids
    ]

    student_rows: list[list[object]] = []
    for s_idx in range(1, num_students + 1):
        sid = f"STU-{s_idx:04d}"
        # Weighted sampling without replacement
        pool = list(class_ids)
        weights = list(popularity_weights)
        chosen: list[str] = []
        for _ in range(num_choices):
            pick = rng.choices(pool, weights=weights, k=1)[0]
            pick_idx = pool.index(pick)
            chosen.append(pick)
            pool.pop(pick_idx)
            weights.pop(pick_idx)
        student_rows.append([sid, *chosen])

    _write_table(classes_out, ["class_id", "max_capacity", "min_capacity"], class_rows)
    _write_table(
        students_out,
        ["student_id", *[f"choice_{i}" for i in range(1, num_choices + 1)]],
        student_rows,
    )
    return students_out, classes_out
