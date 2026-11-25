"""PuLP + HiGHS Mixed-Integer Linear Programming solver for course allocation."""

from __future__ import annotations

import hashlib
import time

import pulp

from algo_deca.models import (
    AppConfig,
    AssignmentRecord,
    ClassInfo,
    ClassSummary,
    InfeasibleAssignmentError,
    SolveResult,
    StudentPreference,
)


def deterministic_tie_break(seed: int, student_id: str, class_id: str, epsilon: float) -> float:
    """Compute a deterministic, row-order-independent tie-break perturbation in [0, epsilon)."""
    if epsilon <= 0.0:
        return 0.0
    digest = hashlib.sha256(f"{seed}:{student_id}:{class_id}".encode()).digest()
    fraction = int.from_bytes(digest[:8], "big") / (1 << 64)
    return fraction * epsilon


def _get_pulp_solver(solver_name: str) -> pulp.LpSolver:
    """Instantiate the configured PuLP MILP solver backend."""
    if solver_name == "highs":
        solver = pulp.HiGHS(msg=False)
        if not solver.available():
            raise RuntimeError(
                "PuLP HiGHS solver is not available. Ensure 'highspy' is installed."
            )
        return solver
    if solver_name == "cbc":
        solver = pulp.PULP_CBC_CMD(msg=False)
        if not solver.available():
            raise RuntimeError("PuLP CBC solver is not available.")
        return solver
    raise ValueError(f"Unsupported solver: {solver_name}")


def solve_assignments(
    students: list[StudentPreference],
    classes: list[ClassInfo],
    config: AppConfig,
    warnings: list[str] | None = None,
) -> SolveResult:
    """Solve the capacitated preference-based course allocation problem to global optimality."""
    start_time = time.perf_counter()
    opt = config.optimization
    num_ranks = len(config.columns.choice_columns)

    # Sort canonically so MILP variable/constraint construction is invariant to input row order.
    sorted_students = sorted(students, key=lambda s: s.student_id)
    sorted_classes = sorted(classes, key=lambda c: c.class_id)
    class_index = {c.class_id: idx for idx, c in enumerate(sorted_classes)}

    prob = pulp.LpProblem("student_class_assignment", pulp.LpMaximize)

    x: dict[tuple[int, int], pulp.LpVariable] = {}
    base_scores: dict[tuple[int, int], float] = {}
    tie_scores: dict[tuple[int, int], float] = {}

    for s_idx, student in enumerate(sorted_students):
        rank_map = {cid: r for r, cid in enumerate(student.choices)}
        if opt.allow_unranked:
            if student.eligible_classes is not None:
                candidate_indices = [class_index[cid] for cid in student.eligible_classes]
            else:
                candidate_indices = list(range(len(sorted_classes)))
        else:
            candidate_indices = [class_index[cid] for cid in student.choices]
        for c_idx in candidate_indices:
            cid = sorted_classes[c_idx].class_id
            var = prob.add_variable(f"x_{s_idx}_{c_idx}", cat=pulp.LpBinary)
            x[(s_idx, c_idx)] = var
            if cid in rank_map:
                base_scores[(s_idx, c_idx)] = opt.rank_weights[rank_map[cid]]
            else:
                base_scores[(s_idx, c_idx)] = opt.unranked_penalty
            tie_scores[(s_idx, c_idx)] = deterministic_tie_break(
                opt.tie_break_seed, student.student_id, cid, opt.tie_break_epsilon
            )

    # Objective: maximize total preference score + tiny deterministic tie-breaker.
    prob += pulp.lpSum(
        (base_scores[key] + tie_scores[key]) * var for key, var in x.items()
    )

    # Constraint 1: each student gets exactly one class.
    for s_idx in range(len(sorted_students)):
        student_vars = [
            x[(s_idx, c_idx)]
            for c_idx in range(len(sorted_classes))
            if (s_idx, c_idx) in x
        ]
        prob += pulp.lpSum(student_vars) == 1, f"student_{s_idx}"

    # Constraint 2: class min/max capacity limits.
    for c_idx, cls in enumerate(sorted_classes):
        class_vars = [
            x[(s_idx, c_idx)]
            for s_idx in range(len(sorted_students))
            if (s_idx, c_idx) in x
        ]
        if not class_vars:
            if cls.min_capacity > 0:
                raise InfeasibleAssignmentError(
                    f"Class '{cls.class_id}' requires min_capacity={cls.min_capacity}, "
                    "but no student is eligible for it (allow_unranked=false)."
                )
            continue
        prob += pulp.lpSum(class_vars) <= cls.max_capacity, f"class_max_{c_idx}"
        if cls.min_capacity > 0:
            prob += pulp.lpSum(class_vars) >= cls.min_capacity, f"class_min_{c_idx}"

    pulp_solver = _get_pulp_solver(opt.solver)
    stats = prob.solve(pulp_solver)
    elapsed = time.perf_counter() - start_time

    status_str = stats.status.name if hasattr(stats.status, "name") else str(stats.status)
    if stats.status != pulp.LpSolveStatus.Optimal:
        detail = (
            "Consider setting allow_unranked = true or increasing class capacities."
            if not opt.allow_unranked
            else "Check class min_capacity, max_capacity, and schedule eligibility constraints."
        )
        raise InfeasibleAssignmentError(
            f"Solver finished with status '{status_str}'. {detail}"
        )

    # Extract assignments keyed by student_id, then return in original input order.
    assigned_by_student: dict[str, AssignmentRecord] = {}
    assigned_counts = {c.class_id: 0 for c in sorted_classes}
    total_pref_score = 0.0
    total_obj_with_tie = float(stats.objective if stats.objective is not None else 0.0)

    rank_counts: dict[str, int] = {str(r): 0 for r in range(1, num_ranks + 1)}
    rank_counts["UNRANKED"] = 0

    for s_idx, student in enumerate(sorted_students):
        chosen_cid: str | None = None
        chosen_score = 0.0
        for c_idx, cls in enumerate(sorted_classes):
            var = x.get((s_idx, c_idx))
            if var is not None and (pulp.value(var) or 0.0) > 0.5:
                chosen_cid = cls.class_id
                chosen_score = base_scores[(s_idx, c_idx)]
                break

        if chosen_cid is None:
            raise InfeasibleAssignmentError(
                f"Internal error: student '{student.student_id}' has no assigned class."
            )

        assigned_counts[chosen_cid] += 1
        total_pref_score += chosen_score

        if chosen_cid in student.choices:
            rank_num = student.choices.index(chosen_cid) + 1
            rank_counts[str(rank_num)] += 1
        else:
            rank_num = None
            rank_counts["UNRANKED"] += 1

        assigned_by_student[student.student_id] = AssignmentRecord(
            student_id=student.student_id,
            assigned_class=chosen_cid,
            assigned_rank=rank_num,
            preference_score=chosen_score,
            choices=student.choices,
        )

    ordered_assignments = [assigned_by_student[s.student_id] for s in students]

    first_choice_demand = {c.class_id: 0 for c in sorted_classes}
    total_mentions = {c.class_id: 0 for c in sorted_classes}
    for student in students:
        if student.choices:
            first_choice_demand[student.choices[0]] += 1
        for cid in student.choices:
            total_mentions[cid] += 1

    class_summaries: list[ClassSummary] = []
    for cls in sorted_classes:
        count = assigned_counts[cls.class_id]
        util_pct = (count / cls.max_capacity * 100.0) if cls.max_capacity > 0 else 0.0
        fc_demand = first_choice_demand[cls.class_id]
        is_bottleneck = fc_demand > cls.max_capacity or (
            cls.max_capacity > 0 and count == cls.max_capacity and fc_demand >= cls.max_capacity
        )
        class_summaries.append(
            ClassSummary(
                class_id=cls.class_id,
                assigned_count=count,
                min_capacity=cls.min_capacity,
                max_capacity=cls.max_capacity,
                utilization_pct=round(util_pct, 2),
                first_choice_demand=fc_demand,
                total_mentions=total_mentions[cls.class_id],
                is_bottleneck=is_bottleneck,
            )
        )

    return SolveResult(
        status=status_str,
        solver_used=opt.solver,
        solve_time_seconds=round(elapsed, 4),
        total_preference_score=round(total_pref_score, 6),
        objective_with_tie_break=round(total_obj_with_tie, 6),
        assignments=ordered_assignments,
        rank_counts=rank_counts,
        class_summaries=class_summaries,
        warnings=list(warnings or []),
    )
