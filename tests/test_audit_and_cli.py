"""Tests for audit artifacts, SHA-256 tamper detection, explainability, and Typer CLI."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from typer.testing import CliRunner

from algo_deca.audit import explain_student_assignment, write_run_artifacts
from algo_deca.cli import app
from algo_deca.io_utils import load_and_validate_inputs
from algo_deca.models import AppConfig
from algo_deca.sample_data import generate_sample_dataset
from algo_deca.solver import solve_assignments

runner = CliRunner()


def test_audit_artifacts_and_tamper_detection(tmp_path: Path) -> None:
    s_path, c_path = generate_sample_dataset(
        tmp_path / "students.csv",
        tmp_path / "classes.csv",
        num_students=30,
        num_classes=6,
        num_choices=5,
        seed=7,
    )
    cfg = AppConfig()
    students, classes, warnings = load_and_validate_inputs(s_path, c_path, cfg)
    res = solve_assignments(students, classes, cfg, warnings=warnings)

    artifacts = write_run_artifacts(
        result=res,
        config=cfg,
        students_path=s_path,
        classes_path=c_path,
        output_dir=tmp_path / "outputs",
    )

    # Valid UUIDv4
    parsed_uuid = uuid.UUID(artifacts.run_id)
    assert parsed_uuid.version == 4
    assert artifacts.assignments_csv.exists()
    assert artifacts.manifest_json.exists()
    assert artifacts.report_md.exists()

    manifest = json.loads(artifacts.manifest_json.read_text(encoding="utf-8"))
    assert manifest["run_id"] == artifacts.run_id
    assert manifest["summary"]["total_students"] == 30

    # Explain works and confirms SHA-256 integrity
    explained = explain_student_assignment(artifacts.run_dir, students[0].student_id)
    assert explained["csv_integrity_ok"] is True

    # Tampering with CSV is detected by explain
    original_text = artifacts.assignments_csv.read_text(encoding="utf-8")
    artifacts.assignments_csv.write_text(original_text + "\n", encoding="utf-8")
    tampered = explain_student_assignment(artifacts.run_dir, students[0].student_id)
    assert tampered["csv_integrity_ok"] is False


def test_cli_end_to_end(tmp_path: Path) -> None:
    s_path = tmp_path / "students.csv"
    c_path = tmp_path / "classes.csv"
    out_dir = tmp_path / "outputs"

    gen_res = runner.invoke(
        app,
        [
            "generate-sample",
            "--students-out",
            str(s_path),
            "--classes-out",
            str(c_path),
            "--num-students",
            "40",
            "--num-classes",
            "6",
        ],
    )
    assert gen_res.exit_code == 0, gen_res.output

    val_res = runner.invoke(
        app,
        ["validate", "--students", str(s_path), "--classes", str(c_path)],
    )
    assert val_res.exit_code == 0, val_res.output
    assert "Validation passed" in val_res.output

    solve_res = runner.invoke(
        app,
        [
            "solve",
            "--students",
            str(s_path),
            "--classes",
            str(c_path),
            "--output-dir",
            str(out_dir),
        ],
    )
    assert solve_res.exit_code == 0, solve_res.output
    assert "Optimal" in solve_res.output

    run_dirs = list(out_dir.glob("run_*"))
    assert len(run_dirs) == 1

    explain_res = runner.invoke(app, ["explain", str(run_dirs[0]), "STU-0001"])
    assert explain_res.exit_code == 0, explain_res.output
    assert "VERIFIED" in explain_res.output
