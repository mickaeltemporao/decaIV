# algo-deca

Student class assignment optimizer using **PuLP + HiGHS** and **convex preference weights**.

Every optimization run produces a **UUID-tagged, UTC-timestamped** output folder containing the assignment CSV, a SHA-256-verified JSON audit manifest (with all input hashes and resolved parameters), and a human-readable Markdown report.

---

## Quick Start

```bash
# 1. Install dependencies into .venv
uv sync

# 2. Validate inputs and config.toml
uv run algo-deca validate

# 3. Run the optimization (writes to outputs/run_<timestamp>_<short_uuid>/)
uv run algo-deca solve

# 4. Explain why a specific student did not receive a higher-ranked choice
uv run algo-deca explain outputs/run_<timestamp>_<short_uuid> STU-0001
```

---

## Input File Formats

Both **`.csv`** (UTF-8 / UTF-8-BOM) and **`.xlsx`** (Excel, first sheet) are supported. Column names are configurable in `config.toml`.

### 1. Students File (`examples/students.csv`)

| `student_id` | `choice_1` | `choice_2` | `choice_3` | `choice_4` | `choice_5` |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `STU-0001` | `CLS-03` | `CLS-01` | `CLS-05` | `CLS-02` | `CLS-08` |

- `student_id` must be unique and non-empty.
- Each student must rank at least 1 class, with no duplicate classes in their row.
- Partial rankings (fewer than 5 choices) are supported and trigger a non-fatal warning.
- **Optional schedule eligibility columns:**
  - `eligible_classes`: semicolon-separated list of class IDs the student is allowed to take (e.g., `CLS-01;CLS-02;CLS-05`).
  - `unavailable_slots`: semicolon-separated time slots the student cannot attend (e.g., `MON_AM;WED_PM`), matched against `time_slot` in the classes file.
  - When present, both ranked choices and any unranked fallback (`allow_unranked = true`) are strictly restricted to eligible classes.

### 2. Classes File (`examples/classes.csv`)

| `class_id` | `max_capacity` | `min_capacity` |
| :--- | ---: | ---: |
| `CLS-01` | 21 | 10 |

- `class_id` must be unique and non-empty.
- `max_capacity` and `min_capacity` are optional per row if `default_max_capacity` / `default_min_capacity` are set in `config.toml` (per-class values override the defaults).
- Optional `time_slot` column (e.g., `MON_AM`) can be paired with `unavailable_slots` in the students file.

---

## Configuration (`config.toml`)

```toml
[paths]
students_file = "examples/students.csv"
classes_file = "examples/classes.csv"
output_dir = "outputs"

[columns]
student_id = "student_id"
choice_columns = ["choice_1", "choice_2", "choice_3", "choice_4", "choice_5"]
class_id = "class_id"
max_capacity = "max_capacity"
min_capacity = "min_capacity"

[optimization]
rank_weights = [100.0, 80.0, 55.0, 25.0, 5.0]
default_max_capacity = 21
default_min_capacity = 10
allow_unranked = true
unranked_penalty = -500.0
tie_break_seed = 42
tie_break_epsilon = 1e-5
solver = "highs"
```

> **Scaling from top-5 to top-14 (or any N) choices:** No code changes are needed—simply update `choice_columns` and `rank_weights` in `config.toml` to have matching lengths.

### Why PuLP + HiGHS?

- **PuLP** provides clean, readable algebraic syntax for defining binary assignment variables, preference objectives, and capacity/schedule constraints—making the math easy to inspect, audit, and extend.
- **HiGHS (`highspy`)** is a fast, modern, open-source MILP solver installed directly as a Python wheel via `uv` (no external system binaries required). Unlike greedy heuristics, it proves **global optimality** for 300+ students in a fraction of a second.

### Why Convex Weights?

Linear weights (`5, 4, 3, 2, 1`) treat dropping one student from 2nd to 5th (`-3`) as equal to bumping three students from 2nd to 1st (`+3`). Convex weights (`100, 80, 55, 25, 5`) have larger drops at lower ranks, discouraging the solver from sacrificing one student to a low-ranked or unranked choice for marginal gains elsewhere.

### Deterministic Tie-Breaking

When multiple assignments achieve the exact same total preference score, `tie_break_seed` and `tie_break_epsilon` add a tiny SHA-256-derived perturbation (`< 1e-5`) per `(seed, student_id, class_id)`. This guarantees:
- **Reproducibility:** Re-running with the same inputs and seed yields the exact same assignments.
- **Row-order independence:** Shuffling rows in `students.csv` or `classes.csv` does not change who gets which class.

---

## CLI Reference

| Command | Description |
| :--- | :--- |
| `uv run algo-deca validate` | Validate `config.toml`, student rankings, and class capacities. |
| `uv run algo-deca solve` | Solve to global optimality and write audited artifacts. |
| `uv run algo-deca explain <run_dir> <student_id>` | Verify CSV SHA-256 integrity and explain why a student missed higher choices. |
| `uv run algo-deca generate-sample` | Generate a synthetic dataset (default: 300 students, 15 classes, 5 choices). |

Common `solve` overrides:
- `--config / -c`: Path to an alternate TOML config.
- `--students / -s` and `--classes / -k`: Override input files.
- `--output-dir / -o`: Override output directory.
- `--seed`: Override `tie_break_seed`.
- `--solver`: Choose `highs` (default) or `cbc`.
- `--allow-unranked / --no-unranked`: Allow or forbid assigning students to unranked classes.

---

## Audit & Dispute Resolution

Each `solve` run creates `outputs/run_<YYYYMMDD_HHMMSS>_<short_uuid>/` containing:
1. **`assignments_<stamp>_<short_uuid>.csv`**: Includes `run_id` (UUIDv4), `run_timestamp_utc`, `student_id`, `assigned_class`, `assigned_rank`, `preference_score`, and the student's original choices.
2. **`audit_manifest_<stamp>_<short_uuid>.json`**: Records software versions, SHA-256 hashes of all inputs and the output CSV, resolved parameters, solver runtime/status, rank distribution, and class bottleneck metrics.
3. **`report_<stamp>_<short_uuid>.md`**: Human-readable summary of parameters, fill rates, and bottleneck classes.

To audit a disputed assignment, run `uv run algo-deca explain <run_dir> <student_id>` — it verifies the CSV has not been altered since the run and shows which higher-ranked choices were at capacity and how many assigned students ranked those classes as high or higher.
