"""Tests for configuration loading, CSV/XLSX parsing, and input validation."""

from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest

from algo_deca.io_utils import load_and_validate_inputs, load_config
from algo_deca.models import AppConfig, DataValidationError


def _write_csv(path: Path, text: str) -> Path:
    path.write_text(text.lstrip(), encoding="utf-8")
    return path


def test_config_rejects_unknown_keys(tmp_path: Path) -> None:
    cfg_file = tmp_path / "bad_config.toml"
    cfg_file.write_text(
        """
[optimization]
unknown_param = 123
""",
        encoding="utf-8",
    )
    with pytest.raises(DataValidationError):
        load_config(cfg_file)


def test_config_rejects_mismatched_weights_length(tmp_path: Path) -> None:
    cfg_file = tmp_path / "bad_weights.toml"
    cfg_file.write_text(
        """
[columns]
choice_columns = ["choice_1", "choice_2", "choice_3"]

[optimization]
rank_weights = [100.0, 50.0]
""",
        encoding="utf-8",
    )
    with pytest.raises(DataValidationError, match="rank_weights length"):
        load_config(cfg_file)


def test_config_rejects_bad_unranked_penalty(tmp_path: Path) -> None:
    cfg_file = tmp_path / "bad_penalty.toml"
    cfg_file.write_text(
        """
[optimization]
allow_unranked = true
unranked_penalty = 10.0
""",
        encoding="utf-8",
    )
    with pytest.raises(DataValidationError, match="unranked_penalty"):
        load_config(cfg_file)


def test_load_csv_with_bom_and_leading_zeros(tmp_path: Path) -> None:
    classes_path = tmp_path / "classes.csv"
    classes_path.write_bytes(
        "\ufeffclass_id,max_capacity,min_capacity\n01,2,0\n02,2,0\n".encode("utf-8")
    )
    students_path = tmp_path / "students.csv"
    students_path.write_bytes(
        "\ufeffstudent_id,choice_1,choice_2\n0042,01,02\n0043,02,01\n".encode("utf-8")
    )
    cfg = AppConfig(
        columns={"choice_columns": ["choice_1", "choice_2"]},
        optimization={"rank_weights": [100.0, 50.0]},
    )
    students, classes, warnings = load_and_validate_inputs(students_path, classes_path, cfg)
    assert [s.student_id for s in students] == ["0042", "0043"]
    assert [c.class_id for c in classes] == ["01", "02"]
    assert warnings == []


def test_load_xlsx_preserves_leading_zeros_and_strips_float_dot_zero(tmp_path: Path) -> None:
    wb_cls = openpyxl.Workbook()
    ws_cls = wb_cls.active
    ws_cls.append(["class_id", "max_capacity"])
    ws_cls.append(["01", 2.0])
    ws_cls.append(["02", 2.0])
    classes_path = tmp_path / "classes.xlsx"
    wb_cls.save(classes_path)

    wb_stu = openpyxl.Workbook()
    ws_stu = wb_stu.active
    ws_stu.append(["student_id", "choice_1", "choice_2"])
    ws_stu.append(["0007", "01", "02"])
    ws_stu.append([101.0, "02", "01"])
    students_path = tmp_path / "students.xlsx"
    wb_stu.save(students_path)

    cfg = AppConfig(
        columns={"choice_columns": ["choice_1", "choice_2"]},
        optimization={"rank_weights": [100.0, 50.0]},
    )
    students, classes, _ = load_and_validate_inputs(students_path, classes_path, cfg)
    assert [s.student_id for s in students] == ["0007", "101"]
    assert [c.max_capacity for c in classes] == [2, 2]


def test_validation_catches_duplicates_unknown_classes_and_capacity_errors(tmp_path: Path) -> None:
    classes_path = _write_csv(
        tmp_path / "classes.csv",
        """
class_id,max_capacity,min_capacity
C1,1,0
C1,2,0
C2,1,2
""",
    )
    students_path = _write_csv(
        tmp_path / "students.csv",
        """
student_id,choice_1,choice_2
S1,C1,C1
S2,UNKNOWN,C1
S2,,
""",
    )
    cfg = AppConfig(
        columns={"choice_columns": ["choice_1", "choice_2"]},
        optimization={"rank_weights": [100.0, 50.0]},
    )
    with pytest.raises(DataValidationError) as exc_info:
        load_and_validate_inputs(students_path, classes_path, cfg)

    joined = "\n".join(exc_info.value.errors)
    assert "duplicate class_id 'C1'" in joined
    assert "min_capacity (2) > max_capacity (1)" in joined
    assert "duplicate class 'C1'" in joined
    assert "unknown class 'UNKNOWN'" in joined
    assert "duplicate student_id 'S2'" in joined


def test_validation_catches_global_capacity_shortage(tmp_path: Path) -> None:
    classes_path = _write_csv(
        tmp_path / "classes.csv",
        """
class_id,max_capacity
C1,1
""",
    )
    students_path = _write_csv(
        tmp_path / "students.csv",
        """
student_id,choice_1
S1,C1
S2,C1
""",
    )
    cfg = AppConfig(
        columns={"choice_columns": ["choice_1"]},
        optimization={"rank_weights": [100.0]},
    )
    with pytest.raises(DataValidationError, match="Insufficient total class capacity"):
        load_and_validate_inputs(students_path, classes_path, cfg)


def test_default_capacities_fallback_when_columns_omitted(tmp_path: Path) -> None:
    classes_path = _write_csv(
        tmp_path / "classes.csv",
        """
class_id
C1
C2
""",
    )
    students_path = _write_csv(
        tmp_path / "students.csv",
        """
student_id,choice_1
S1,C1
S2,C2
""",
    )
    cfg = AppConfig(
        columns={"choice_columns": ["choice_1"]},
        optimization={
            "rank_weights": [100.0],
            "default_max_capacity": 21,
            "default_min_capacity": 1,
        },
    )
    _, classes, _ = load_and_validate_inputs(students_path, classes_path, cfg)
    assert [(c.max_capacity, c.min_capacity) for c in classes] == [(21, 1), (21, 1)]


def test_schedule_eligibility_validation_rejects_conflicts(tmp_path: Path) -> None:
    classes_path = _write_csv(
        tmp_path / "classes.csv",
        """
class_id,max_capacity,time_slot
C1,5,MON_AM
C2,5,TUE_PM
""",
    )
    students_path = _write_csv(
        tmp_path / "students.csv",
        """
student_id,choice_1,eligible_classes,unavailable_slots
S1,C2,C1,
S2,C1,,MON_AM
""",
    )
    cfg = AppConfig(
        columns={"choice_columns": ["choice_1"]},
        optimization={"rank_weights": [100.0]},
    )
    with pytest.raises(DataValidationError) as exc_info:
        load_and_validate_inputs(students_path, classes_path, cfg)

    joined = "\n".join(exc_info.value.errors)
    assert "S1" in joined
    assert "S2" in joined
    assert "schedule eligibility" in joined
