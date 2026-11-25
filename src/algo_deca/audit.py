"""Audit artifact generation (UUID, UTC timestamp, SHA-256 manifest, report) and explainability."""

from __future__ import annotations

import csv
import json
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pulp

from algo_deca import __version__
from algo_deca.io_utils import sha256_file
from algo_deca.models import AppConfig, SolveResult


@dataclass(frozen=True)
class RunArtifacts:
    """Paths and metadata for a completed optimization run."""

    run_id: str
    short_id: str
    timestamp_utc: str
    run_dir: Path
    assignments_csv: Path
    manifest_json: Path
    report_md: Path


def write_run_artifacts(
    result: SolveResult,
    config: AppConfig,
    students_path: Path,
    classes_path: Path,
    output_dir: Path,
    config_path: Path | None = None,
) -> RunArtifacts:
    """Write timestamped, UUID-tagged CSV, JSON audit manifest, and Markdown report."""
    now = datetime.now(UTC)
    run_id = str(uuid.uuid4())
    short_id = run_id[:8]
    timestamp_utc = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    file_stamp = now.strftime("%Y%m%d_%H%M%S")
    stem = f"{file_stamp}_{short_id}"

    run_dir = output_dir / f"run_{stem}"
    run_dir.mkdir(parents=True, exist_ok=True)

    assignments_csv = run_dir / f"assignments_{stem}.csv"
    manifest_json = run_dir / f"audit_manifest_{stem}.json"
    report_md = run_dir / f"report_{stem}.md"

    choice_cols = config.columns.choice_columns
    headers = [
        "run_id",
        "run_timestamp_utc",
        "student_id",
        "assigned_class",
        "assigned_rank",
        "preference_score",
        *choice_cols,
    ]
    with assignments_csv.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers)
        for rec in result.assignments:
            padded_choices = list(rec.choices) + [""] * max(0, len(choice_cols) - len(rec.choices))
            writer.writerow(
                [
                    run_id,
                    timestamp_utc,
                    rec.student_id,
                    rec.assigned_class,
                    str(rec.assigned_rank) if rec.assigned_rank is not None else "UNRANKED",
                    rec.preference_score,
                    *padded_choices[: len(choice_cols)],
                ]
            )

    csv_sha256 = sha256_file(assignments_csv)
    students_sha256 = sha256_file(students_path)
    classes_sha256 = sha256_file(classes_path)
    config_sha256 = sha256_file(config_path) if config_path and config_path.exists() else None

    total_students = len(result.assignments)
    rank_percentages = {
        k: round((v / total_students * 100.0) if total_students else 0.0, 2)
        for k, v in result.rank_counts.items()
    }
    bottleneck_classes = [c.class_id for c in result.class_summaries if c.is_bottleneck]

    manifest_data: dict[str, Any] = {
        "run_id": run_id,
        "short_id": short_id,
        "timestamp_utc": timestamp_utc,
        "versions": {
            "algo_deca": __version__,
            "python": sys.version.split()[0],
            "pulp": getattr(pulp, "__version__", "unknown"),
        },
        "inputs": {
            "students_file": str(students_path),
            "students_sha256": students_sha256,
            "classes_file": str(classes_path),
            "classes_sha256": classes_sha256,
            "config_file": str(config_path) if config_path else None,
            "config_sha256": config_sha256,
        },
        "outputs": {
            "assignments_csv": assignments_csv.name,
            "assignments_sha256": csv_sha256,
        },
        "parameters": config.optimization.model_dump(),
        "columns": config.columns.model_dump(),
        "solver": {
            "status": result.status,
            "solver_used": result.solver_used,
            "solve_time_seconds": result.solve_time_seconds,
            "total_preference_score": result.total_preference_score,
            "objective_with_tie_break": result.objective_with_tie_break,
        },
        "summary": {
            "total_students": total_students,
            "total_classes": len(result.class_summaries),
            "mean_preference_score": round(
                (result.total_preference_score / total_students) if total_students else 0.0, 4
            ),
            "rank_counts": result.rank_counts,
            "rank_percentages": rank_percentages,
            "bottleneck_classes": bottleneck_classes,
        },
        "class_summaries": [c.model_dump() for c in result.class_summaries],
        "warnings": result.warnings,
    }
    manifest_json.write_text(json.dumps(manifest_data, indent=2) + "\n", encoding="utf-8")

    # Build human-readable Markdown audit report
    lines = [
        f"# Assignment Audit Report (`{short_id}`)",
        "",
        f"- **Run ID (UUID):** `{run_id}`",
        f"- **Timestamp (UTC):** `{timestamp_utc}`",
        (
            f"- **Status:** `{result.status}` via `{result.solver_used}` "
            f"in `{result.solve_time_seconds:.4f}s`"
        ),
        f"- **Total Students:** {total_students}",
        f"- **Total Preference Score:** {result.total_preference_score:.2f} "
        f"(mean: {manifest_data['summary']['mean_preference_score']:.2f})",
        "",
        "## File Integrity (SHA-256)",
        "",
        f"- `assignments_csv`: `{assignments_csv.name}` — `{csv_sha256}`",
        f"- `students_file`: `{students_path}` — `{students_sha256}`",
        f"- `classes_file`: `{classes_path}` — `{classes_sha256}`",
    ]
    if config_path and config_sha256:
        lines.append(f"- `config_file`: `{config_path}` — `{config_sha256}`")

    lines.extend(
        [
            "",
            "## Optimization Parameters",
            "",
            "| Parameter | Value |",
            "| :--- | :--- |",
            f"| `rank_weights` | `{config.optimization.rank_weights}` |",
            f"| `allow_unranked` | `{config.optimization.allow_unranked}` |",
            f"| `unranked_penalty` | `{config.optimization.unranked_penalty}` |",
            f"| `tie_break_seed` | `{config.optimization.tie_break_seed}` |",
            f"| `tie_break_epsilon` | `{config.optimization.tie_break_epsilon}` |",
            f"| `solver` | `{config.optimization.solver}` |",
            "",
            "## Preference Rank Distribution",
            "",
            "| Choice Rank | Students | Percentage |",
            "| :--- | ---: | ---: |",
        ]
    )
    for rank_key, count in result.rank_counts.items():
        pct = rank_percentages[rank_key]
        label = f"Choice {rank_key}" if rank_key != "UNRANKED" else "Unranked"
        lines.append(f"| {label} | {count} | {pct:.2f}% |")

    lines.extend(
        [
            "",
            "## Class Utilization & Bottlenecks",
            "",
            "| Class | Assigned | Min | Max | Fill % | 1st-Choice Demand | "
            "Total Mentions | Bottleneck |",
            "| :--- | ---: | ---: | ---: | ---: | ---: | ---: | :---: |",
        ]
    )
    for cls in result.class_summaries:
        b_flag = "YES" if cls.is_bottleneck else ""
        lines.append(
            f"| `{cls.class_id}` | {cls.assigned_count} | {cls.min_capacity} | "
            f"{cls.max_capacity} | {cls.utilization_pct:.1f}% | {cls.first_choice_demand} | "
            f"{cls.total_mentions} | {b_flag} |"
        )

    if result.warnings:
        lines.extend(["", "## Warnings", ""])
        for w in result.warnings:
            lines.append(f"- {w}")

    lines.extend(
        [
            "",
            "## Explaining an Individual Assignment",
            "",
            "To audit why a specific student did not receive a higher-ranked choice, run:",
            "",
            "```bash",
            f"uv run deca explain {run_dir} <STUDENT_ID>",
            "```",
            "",
        ]
    )
    report_md.write_text("\n".join(lines), encoding="utf-8")

    return RunArtifacts(
        run_id=run_id,
        short_id=short_id,
        timestamp_utc=timestamp_utc,
        run_dir=run_dir,
        assignments_csv=assignments_csv,
        manifest_json=manifest_json,
        report_md=report_md,
    )


def _locate_manifest(run_path: Path) -> Path:
    """Resolve either a run directory or a manifest JSON path to the manifest file."""
    if not run_path.exists():
        raise FileNotFoundError(f"Run path not found: {run_path}")
    if run_path.is_file():
        return run_path
    manifests = sorted(run_path.glob("audit_manifest_*.json"))
    if not manifests:
        raise FileNotFoundError(f"No audit_manifest_*.json found in {run_path}")
    return manifests[0]


def explain_student_assignment(run_path: Path, student_id: str) -> dict[str, Any]:
    """Load an audited run, verify CSV SHA-256 integrity, and explain a student's assignment."""
    manifest_path = _locate_manifest(run_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    csv_path = manifest_path.parent / manifest["outputs"]["assignments_csv"]
    if not csv_path.exists():
        raise FileNotFoundError(f"Assignments CSV not found: {csv_path}")

    actual_sha = sha256_file(csv_path)
    expected_sha = manifest["outputs"]["assignments_sha256"]
    csv_integrity_ok = actual_sha == expected_sha

    choice_cols: list[str] = manifest["columns"]["choice_columns"]
    rows_by_student: dict[str, dict[str, str]] = {}
    all_rows: list[dict[str, str]] = []
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            all_rows.append(row)
            rows_by_student[row["student_id"]] = row

    if student_id not in rows_by_student:
        raise KeyError(f"Student '{student_id}' not found in {csv_path.name}.")

    target = rows_by_student[student_id]
    student_choices = [target[c] for c in choice_cols if target.get(c, "") != ""]
    assigned_rank_raw = target["assigned_rank"]
    assigned_rank = int(assigned_rank_raw) if assigned_rank_raw != "UNRANKED" else None

    class_summary_map = {c["class_id"]: c for c in manifest["class_summaries"]}

    # Determine how many higher-ranked choices were missed and why.
    limit_idx = (assigned_rank - 1) if assigned_rank is not None else len(student_choices)
    missed_choices: list[dict[str, Any]] = []
    for idx in range(limit_idx):
        cid = student_choices[idx]
        target_rank_num = idx + 1
        cs = class_summary_map[cid]

        assigned_to_cid = [r for r in all_rows if r["assigned_class"] == cid]
        higher_or_equal_count = 0
        for r in assigned_to_cid:
            r_choices = [r[c] for c in choice_cols if r.get(c, "") != ""]
            if cid in r_choices and (r_choices.index(cid) + 1) <= target_rank_num:
                higher_or_equal_count += 1

        missed_choices.append(
            {
                "choice_rank": target_rank_num,
                "class_id": cid,
                "assigned_count": cs["assigned_count"],
                "max_capacity": cs["max_capacity"],
                "is_full": cs["assigned_count"] >= cs["max_capacity"],
                "first_choice_demand": cs["first_choice_demand"],
                "assigned_with_higher_or_equal_rank": higher_or_equal_count,
            }
        )

    return {
        "run_id": manifest["run_id"],
        "timestamp_utc": manifest["timestamp_utc"],
        "csv_file": str(csv_path),
        "csv_integrity_ok": csv_integrity_ok,
        "student_id": student_id,
        "assigned_class": target["assigned_class"],
        "assigned_rank": assigned_rank_raw,
        "preference_score": float(target["preference_score"]),
        "choices": student_choices,
        "missed_higher_choices": missed_choices,
        "parameters": manifest["parameters"],
    }
