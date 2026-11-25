"""Typer CLI for algo-deca."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from algo_deca.audit import explain_student_assignment, write_run_artifacts
from algo_deca.io_utils import load_and_validate_inputs, load_config
from algo_deca.models import AppConfig, DataValidationError, InfeasibleAssignmentError
from algo_deca.sample_data import generate_sample_dataset
from algo_deca.solver import solve_assignments

app = typer.Typer(
    name="deca",
    help="Auditable student class assignment optimizer using PuLP + HiGHS.",
    no_args_is_help=True,
)
console = Console()


def _load_effective_config(
    config_path: Path | None,
    students: Path | None,
    classes: Path | None,
    output_dir: Path | None = None,
    seed: int | None = None,
    solver: str | None = None,
    allow_unranked: bool | None = None,
) -> tuple[AppConfig, Path, Path, Path, Path | None]:
    """Load config.toml (if present) and apply CLI overrides."""
    resolved_config_path: Path | None = None
    if config_path is not None and config_path.exists():
        config = load_config(config_path)
        resolved_config_path = config_path.resolve()
    elif config_path is not None and config_path != Path("config.toml"):
        raise DataValidationError([f"Config file not found: {config_path}"])
    else:
        config = AppConfig()

    if students is not None:
        config.paths.students_file = str(students.resolve())
    if classes is not None:
        config.paths.classes_file = str(classes.resolve())
    if output_dir is not None:
        config.paths.output_dir = str(output_dir.resolve())
    if seed is not None:
        config.optimization.tie_break_seed = seed
    if solver is not None:
        if solver not in ("highs", "cbc"):
            raise DataValidationError([f"Unsupported solver '{solver}'. Use 'highs' or 'cbc'."])
        config.optimization.solver = solver  # type: ignore[assignment]
    if allow_unranked is not None:
        config.optimization.allow_unranked = allow_unranked

    return (
        config,
        Path(config.paths.students_file),
        Path(config.paths.classes_file),
        Path(config.paths.output_dir),
        resolved_config_path,
    )


@app.command()
def validate(
    config: Annotated[Path, typer.Option("--config", "-c", help="Path to config.toml.")] = Path(
        "config.toml"
    ),
    students: Annotated[
        Path | None, typer.Option("--students", "-s", help="Override students CSV/XLSX path.")
    ] = None,
    classes: Annotated[
        Path | None, typer.Option("--classes", "-k", help="Override classes CSV/XLSX path.")
    ] = None,
) -> None:
    """Validate configuration, student preferences, and class capacities."""
    try:
        cfg, students_path, classes_path, _, _ = _load_effective_config(
            config, students, classes
        )
        student_list, class_list, warnings = load_and_validate_inputs(
            students_path, classes_path, cfg
        )
    except DataValidationError as exc:
        console.print(f"[red bold]Validation failed:[/red bold]\n{exc}")
        raise typer.Exit(code=1) from exc

    total_max = sum(c.max_capacity for c in class_list)
    total_min = sum(c.min_capacity for c in class_list)
    console.print(
        Panel(
            f"[green bold]Validation passed[/green bold]\n"
            f"Students: [bold]{len(student_list)}[/bold] | "
            f"Classes: [bold]{len(class_list)}[/bold] | "
            f"Seats (min..max): [bold]{total_min}..{total_max}[/bold]",
            title="deca validate",
        )
    )
    for w in warnings:
        console.print(f"[yellow]Warning:[/yellow] {w}")


@app.command()
def solve(
    config: Annotated[Path, typer.Option("--config", "-c", help="Path to config.toml.")] = Path(
        "config.toml"
    ),
    students: Annotated[
        Path | None, typer.Option("--students", "-s", help="Override students CSV/XLSX path.")
    ] = None,
    classes: Annotated[
        Path | None, typer.Option("--classes", "-k", help="Override classes CSV/XLSX path.")
    ] = None,
    output_dir: Annotated[
        Path | None, typer.Option("--output-dir", "-o", help="Override output directory.")
    ] = None,
    seed: Annotated[
        int | None, typer.Option("--seed", help="Override tie_break_seed.")
    ] = None,
    solver: Annotated[
        str | None, typer.Option("--solver", help="Override solver ('highs' or 'cbc').")
    ] = None,
    allow_unranked: Annotated[
        bool | None,
        typer.Option("--allow-unranked/--no-unranked", help="Allow/forbid unranked assignments."),
    ] = None,
) -> None:
    """Solve the class assignment optimization and write audited output artifacts."""
    try:
        cfg, students_path, classes_path, out_dir, cfg_path = _load_effective_config(
            config, students, classes, output_dir, seed, solver, allow_unranked
        )
        student_list, class_list, warnings = load_and_validate_inputs(
            students_path, classes_path, cfg
        )
        result = solve_assignments(student_list, class_list, cfg, warnings=warnings)
    except (DataValidationError, InfeasibleAssignmentError, RuntimeError) as exc:
        console.print(f"[red bold]Error:[/red bold] {exc}")
        raise typer.Exit(code=1) from exc

    artifacts = write_run_artifacts(
        result=result,
        config=cfg,
        students_path=students_path,
        classes_path=classes_path,
        output_dir=out_dir,
        config_path=cfg_path,
    )

    console.print(
        Panel(
            f"[green bold]Status:[/green bold] {result.status} ({result.solver_used}, "
            f"{result.solve_time_seconds:.4f}s)\n"
            f"[bold]Run ID:[/bold] {artifacts.run_id}\n"
            f"[bold]Timestamp (UTC):[/bold] {artifacts.timestamp_utc}\n"
            f"[bold]Total Preference Score:[/bold] {result.total_preference_score:.2f}",
            title="deca solve",
        )
    )

    rank_table = Table(title="Preference Rank Distribution")
    rank_table.add_column("Choice Rank")
    rank_table.add_column("Students", justify="right")
    rank_table.add_column("Share", justify="right")
    total_n = len(result.assignments)
    for rk, count in result.rank_counts.items():
        pct = (count / total_n * 100.0) if total_n else 0.0
        label = f"Choice {rk}" if rk != "UNRANKED" else "Unranked"
        rank_table.add_row(label, str(count), f"{pct:.1f}%")
    console.print(rank_table)

    class_table = Table(title="Class Utilization & Bottlenecks")
    class_table.add_column("Class")
    class_table.add_column("Assigned", justify="right")
    class_table.add_column("Min..Max", justify="right")
    class_table.add_column("Fill %", justify="right")
    class_table.add_column("1st-Choice Demand", justify="right")
    class_table.add_column("Bottleneck", justify="center")
    for cs in result.class_summaries:
        class_table.add_row(
            cs.class_id,
            str(cs.assigned_count),
            f"{cs.min_capacity}..{cs.max_capacity}",
            f"{cs.utilization_pct:.1f}%",
            str(cs.first_choice_demand),
            "[red]YES[/red]" if cs.is_bottleneck else "-",
        )
    console.print(class_table)

    for w in result.warnings:
        console.print(f"[yellow]Warning:[/yellow] {w}")

    console.print(f"\n[bold]Assignments CSV:[/bold] {artifacts.assignments_csv}")
    console.print(f"[bold]Audit Manifest:[/bold]  {artifacts.manifest_json}")
    console.print(f"[bold]Audit Report:[/bold]    {artifacts.report_md}")


@app.command()
def explain(
    run_path: Annotated[
        Path, typer.Argument(help="Path to a run directory or audit_manifest_*.json.")
    ],
    student_id: Annotated[str, typer.Argument(help="Student ID to explain.")],
) -> None:
    """Explain why a specific student received their assignment and verify CSV integrity."""
    try:
        info = explain_student_assignment(run_path, student_id)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        console.print(f"[red bold]Error:[/red bold] {exc}")
        raise typer.Exit(code=1) from exc

    integrity_badge = (
        "[green bold]VERIFIED (SHA-256 matches manifest)[/green bold]"
        if info["csv_integrity_ok"]
        else "[red bold]MISMATCH (CSV modified after run!)[/red bold]"
    )
    console.print(
        Panel(
            f"[bold]Run ID:[/bold] {info['run_id']}\n"
            f"[bold]Timestamp (UTC):[/bold] {info['timestamp_utc']}\n"
            f"[bold]CSV Integrity:[/bold] {integrity_badge}\n"
            f"[bold]Student:[/bold] {info['student_id']}\n"
            f"[bold]Assigned Class:[/bold] {info['assigned_class']} "
            f"(Rank: {info['assigned_rank']}, Score: {info['preference_score']:.2f})\n"
            f"[bold]Submitted Choices:[/bold] {' > '.join(info['choices'])}",
            title=f"Audit Explanation: {student_id}",
        )
    )

    missed = info["missed_higher_choices"]
    if not missed:
        console.print("[green]Student received their 1st choice.[/green]")
        return

    table = Table(title="Higher-Ranked Choices Not Assigned")
    table.add_column("Rank", justify="right")
    table.add_column("Class")
    table.add_column("Fill (Assigned/Max)", justify="right")
    table.add_column("Full?", justify="center")
    table.add_column("1st-Choice Demand", justify="right")
    table.add_column("Assigned w/ >= Rank", justify="right")
    for m in missed:
        table.add_row(
            str(m["choice_rank"]),
            m["class_id"],
            f"{m['assigned_count']}/{m['max_capacity']}",
            "[red]YES[/red]" if m["is_full"] else "NO",
            str(m["first_choice_demand"]),
            str(m["assigned_with_higher_or_equal_rank"]),
        )
    console.print(table)


@app.command("generate-sample")
def generate_sample(
    students_out: Annotated[
        Path, typer.Option("--students-out", help="Output path for sample students file.")
    ] = Path("examples/students.csv"),
    classes_out: Annotated[
        Path, typer.Option("--classes-out", help="Output path for sample classes file.")
    ] = Path("examples/classes.csv"),
    num_students: Annotated[
        int, typer.Option("--num-students", help="Number of students to generate.")
    ] = 300,
    num_classes: Annotated[
        int, typer.Option("--num-classes", help="Number of classes to generate.")
    ] = 15,
    num_choices: Annotated[
        int, typer.Option("--num-choices", help="Number of ranked choices per student.")
    ] = 5,
    max_capacity: Annotated[
        int | None,
        typer.Option("--max-capacity", help="Max capacity per class (auto-computed if omitted)."),
    ] = None,
    min_capacity: Annotated[
        int | None,
        typer.Option("--min-capacity", help="Min capacity per class (auto-computed if omitted)."),
    ] = None,
    seed: Annotated[int, typer.Option("--seed", help="Random seed.")] = 2026,
) -> None:
    """Generate a realistic sample dataset (default: 300 students, 15 classes, 5 choices)."""
    try:
        s_path, c_path = generate_sample_dataset(
            students_out=students_out,
            classes_out=classes_out,
            num_students=num_students,
            num_classes=num_classes,
            num_choices=num_choices,
            max_capacity=max_capacity,
            min_capacity=min_capacity,
            seed=seed,
        )
    except ValueError as exc:
        console.print(f"[red bold]Error:[/red bold] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(
        f"[green]Generated sample dataset:[/green] {s_path} ({num_students} students) "
        f"and {c_path} ({num_classes} classes)."
    )


if __name__ == "__main__":
    app()
