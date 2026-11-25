"""Tests for PuLP + HiGHS optimization model, fairness, determinism, and performance."""

from __future__ import annotations

import random

import pytest

from algo_deca.io_utils import load_and_validate_inputs
from algo_deca.models import (
    AppConfig,
    ClassInfo,
    InfeasibleAssignmentError,
    StudentPreference,
)
from algo_deca.sample_data import generate_sample_dataset
from algo_deca.solver import solve_assignments


def test_exact_optimum_on_small_instance() -> None:
    classes = [
        ClassInfo(class_id="A", max_capacity=1, min_capacity=0),
        ClassInfo(class_id="B", max_capacity=1, min_capacity=0),
    ]
    # Both want A first; S1 only ranks A, B; S2 prefers A slightly less or same -> optimal is 180
    students = [
        StudentPreference(student_id="S1", choices=["A", "B"]),
        StudentPreference(student_id="S2", choices=["A", "B"]),
    ]
    cfg = AppConfig(
        columns={"choice_columns": ["choice_1", "choice_2"]},
        optimization={"rank_weights": [100.0, 80.0]},
    )
    res = solve_assignments(students, classes, cfg)
    assert res.status == "Optimal"
    assert res.total_preference_score == 180.0
    assert {a.assigned_class for a in res.assignments} == {"A", "B"}


def test_convex_weights_prevent_sacrificing_one_student_to_unranked() -> None:
    # 3 classes of capacity 1 each; 3 students.
    # S1: [A, B, C]
    # S2: [A, C, B]
    # S3: [A, B, C]
    # No matter what, one gets 1st, one gets 2nd, one gets 3rd:
    # Actually let's test a case where linear weights would drop S3 to 3rd to bump S1 & S2,
    # whereas convex weights prefer two 2nds over one 1st + one 3rd:
    # Suppose S1 wants A (1st) or B (2nd), S2 wants A (1st) or C (2nd)...
    # Direct test: S1=[A, B], S2=[B, C], S3=[A, C] with cap 1 each.
    # Assignment 1: S1->A(1), S2->B(1), S3->C(2) => ranks (1,1,2) -> always best.
    # Better test: 2 students, 3 classes (A:1, B:1, C:1).
    # Wait: S1=[A, B, C], S2=[A, C, B].
    # Better: S1=[A, B, C], S2=[B, C, A], S3=[A, C, B] - no, let's construct:
    # Classes: A(1), B(1), C(1).
    # S1: [A, B, C], S2: [A, B, C], S3: [B, A, C] -> still symmetric.
    # Let's test min_capacity + convex vs linear directly:
    # Suppose S1 has choices [A, B, C] (1st=A, 2nd=B, 3rd=C), S2 has [A, B, C].
    # With convex [100, 80, 20]: 2*80 = 160 > 100 + 20 = 120.
    # With linear [100, 60, 20]: 2*60 = 120 == 100 + 20 = 120.
    # If S1=[A, B, C], S2=[A, B, C], and A has cap 1, B has cap 2, C has cap 1:
    # Actually if B has cap 2, S1->A, S2->B gives 100+80=180.
    # Make A cap 1, B cap 2, C cap 1, and S3=[A, B, C] with S1=[C, B, A]? No:
    # S1=[A, B, C], S2=[A, B, C], S3=[B, C, A]:
    # S1->A(1), S2->B(2), S3->B(1) => 100 + 80 + 100 = 280.
    # Either way, verify convex weights give expected preference score on a 3-choice config.
    classes = [
        ClassInfo(class_id="A", max_capacity=1),
        ClassInfo(class_id="B", max_capacity=1),
        ClassInfo(class_id="C", max_capacity=1),
    ]
    students = [
        StudentPreference(student_id="S1", choices=["A", "B", "C"]),
        StudentPreference(student_id="S2", choices=["A", "B", "C"]),
        StudentPreference(student_id="S3", choices=["B", "C", "A"]),
    ]
    cfg = AppConfig(
        columns={"choice_columns": ["choice_1", "choice_2", "choice_3"]},
        optimization={"rank_weights": [100.0, 80.0, 20.0]},
    )
    res = solve_assignments(students, classes, cfg)
    assert res.status == "Optimal"
    # Best possible:
    # S1->A(100), S3->B(100), S2->C(20) = 220 vs S1->A(100), S2->B(80), S3->C(80) = 260!
    # Convex weights choose (100 + 80 + 80 = 260) instead of (100 + 100 + 20 = 220).
    assert res.total_preference_score == 260.0
    by_id = {a.student_id: a for a in res.assignments}
    assert by_id["S3"].assigned_class == "C"
    assert by_id["S3"].assigned_rank == 2


def test_min_capacity_and_unranked_behavior() -> None:
    classes = [
        ClassInfo(class_id="POPULAR", max_capacity=1, min_capacity=0),
        ClassInfo(class_id="NICHE", max_capacity=2, min_capacity=1),
    ]
    students = [
        StudentPreference(student_id="S1", choices=["POPULAR"]),
        StudentPreference(student_id="S2", choices=["POPULAR"]),
    ]
    cfg_allow = AppConfig(
        columns={"choice_columns": ["choice_1"]},
        optimization={"rank_weights": [100.0], "allow_unranked": True, "unranked_penalty": -500.0},
    )
    res = solve_assignments(students, classes, cfg_allow)
    assert res.rank_counts == {"1": 1, "UNRANKED": 1}
    assert res.total_preference_score == -400.0

    cfg_forbid = AppConfig(
        columns={"choice_columns": ["choice_1"]},
        optimization={"rank_weights": [100.0], "allow_unranked": False},
    )
    with pytest.raises(InfeasibleAssignmentError):
        solve_assignments(students, classes, cfg_forbid)


def test_determinism_and_row_order_invariance() -> None:
    classes = [ClassInfo(class_id=f"C{i}", max_capacity=2) for i in range(4)]
    students = [
        StudentPreference(student_id=f"S{i}", choices=["C0", "C1", "C2"])
        for i in range(6)
    ]
    cfg = AppConfig(
        columns={"choice_columns": ["choice_1", "choice_2", "choice_3"]},
        optimization={"rank_weights": [100.0, 80.0, 55.0], "tie_break_seed": 123},
    )
    res1 = solve_assignments(students, classes, cfg)

    rng = random.Random(999)
    shuffled_students = list(students)
    shuffled_classes = list(classes)
    rng.shuffle(shuffled_students)
    rng.shuffle(shuffled_classes)

    res2 = solve_assignments(shuffled_students, shuffled_classes, cfg)

    map1 = {a.student_id: a.assigned_class for a in res1.assignments}
    map2 = {a.student_id: a.assigned_class for a in res2.assignments}
    assert map1 == map2


def test_schedule_eligibility_restricts_unranked_fallback() -> None:
    classes = [
        ClassInfo(class_id="C1", max_capacity=1),
        ClassInfo(class_id="C2", max_capacity=1),
        ClassInfo(class_id="C3", max_capacity=1),
    ]
    # S1 gets C1. S2 also wanted C1, is eligible for C2 (not C3), so must get C2 (unranked).
    students = [
        StudentPreference(student_id="S1", choices=["C1"], eligible_classes=["C1", "C2", "C3"]),
        StudentPreference(student_id="S2", choices=["C1"], eligible_classes=["C1", "C2"]),
    ]
    cfg = AppConfig(
        columns={"choice_columns": ["choice_1"]},
        optimization={"rank_weights": [100.0], "allow_unranked": True, "unranked_penalty": -500.0},
    )
    res = solve_assignments(students, classes, cfg)
    by_id = {a.student_id: a for a in res.assignments}
    assert by_id["S2"].assigned_class == "C2"

    # If S2 is ONLY eligible for C1 (which S1 takes), solver must raise InfeasibleAssignmentError.
    students_infeasible = [
        StudentPreference(student_id="S1", choices=["C1"], eligible_classes=["C1"]),
        StudentPreference(student_id="S2", choices=["C1"], eligible_classes=["C1"]),
    ]
    with pytest.raises(InfeasibleAssignmentError):
        solve_assignments(students_infeasible, classes, cfg)


def test_300_students_15_classes_sample_solves_fast_and_respects_all_constraints(tmp_path) -> None:
    s_path, c_path = generate_sample_dataset(
        tmp_path / "students.csv",
        tmp_path / "classes.csv",
        num_students=300,
        num_classes=15,
        num_choices=5,
        seed=2026,
    )
    cfg = AppConfig()
    students, classes, _ = load_and_validate_inputs(s_path, c_path, cfg)
    res = solve_assignments(students, classes, cfg)
    assert res.status == "Optimal"
    assert len(res.assignments) == 300
    assert len(res.class_summaries) == 15
    assert res.solve_time_seconds < 5.0
    for cs in res.class_summaries:
        assert cs.min_capacity == 10
        assert cs.max_capacity == 21
        assert 10 <= cs.assigned_count <= 21
