"""Pydantic models and domain exceptions for algo-deca."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DataValidationError(ValueError):
    """Raised when input files or configuration fail validation."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        summary = "\n".join(f"  - {err}" for err in errors)
        super().__init__(f"Validation failed with {len(errors)} error(s):\n{summary}")


class InfeasibleAssignmentError(RuntimeError):
    """Raised when no feasible assignment exists under the given constraints."""


class PathsConfig(BaseModel):
    """File paths configuration."""

    model_config = ConfigDict(extra="forbid")

    students_file: str = "examples/students.csv"
    classes_file: str = "examples/classes.csv"
    output_dir: str = "outputs"


class ColumnsConfig(BaseModel):
    """Column mapping configuration for student and class tabular files."""

    model_config = ConfigDict(extra="forbid")

    student_id: str = "student_id"
    choice_columns: list[str] = Field(
        default_factory=lambda: ["choice_1", "choice_2", "choice_3", "choice_4", "choice_5"]
    )
    eligible_classes: str = "eligible_classes"
    unavailable_slots: str = "unavailable_slots"
    class_id: str = "class_id"
    max_capacity: str = "max_capacity"
    min_capacity: str = "min_capacity"
    time_slot: str = "time_slot"

    @field_validator("choice_columns")
    @classmethod
    def validate_choice_columns(cls, value: list[str]) -> list[str]:
        if len(value) < 1:
            raise ValueError("choice_columns must contain at least one column.")
        if len(set(value)) != len(value):
            raise ValueError("choice_columns must not contain duplicate column names.")
        return value


class OptimizationConfig(BaseModel):
    """Optimization solver and preference weighting configuration."""

    model_config = ConfigDict(extra="forbid")

    rank_weights: list[float] = Field(default_factory=lambda: [100.0, 80.0, 55.0, 25.0, 5.0])
    default_max_capacity: int | None = None
    default_min_capacity: int = 0
    allow_unranked: bool = True
    unranked_penalty: float = -500.0
    tie_break_seed: int = 42
    tie_break_epsilon: float = 1e-5
    solver: Literal["highs", "cbc"] = "highs"

    @model_validator(mode="after")
    def validate_default_capacities(self) -> OptimizationConfig:
        if self.default_max_capacity is not None and self.default_max_capacity < 0:
            raise ValueError("default_max_capacity must be >= 0.")
        if self.default_min_capacity < 0:
            raise ValueError("default_min_capacity must be >= 0.")
        if (
            self.default_max_capacity is not None
            and self.default_min_capacity > self.default_max_capacity
        ):
            raise ValueError(
                f"default_min_capacity ({self.default_min_capacity}) must be <= "
                f"default_max_capacity ({self.default_max_capacity})."
            )
        return self

    @field_validator("rank_weights")
    @classmethod
    def validate_rank_weights(cls, value: list[float]) -> list[float]:
        if len(value) < 1:
            raise ValueError("rank_weights must contain at least one weight.")
        for i in range(len(value) - 1):
            if value[i] < value[i + 1]:
                raise ValueError(
                    f"rank_weights must be non-increasing, but index {i} ({value[i]}) "
                    f"< index {i + 1} ({value[i + 1]})."
                )
        return value

    @field_validator("tie_break_epsilon")
    @classmethod
    def validate_tie_break_epsilon(cls, value: float) -> float:
        if value < 0:
            raise ValueError("tie_break_epsilon must be >= 0.")
        return value


class AppConfig(BaseModel):
    """Top-level application configuration."""

    model_config = ConfigDict(extra="forbid")

    paths: PathsConfig = Field(default_factory=PathsConfig)
    columns: ColumnsConfig = Field(default_factory=ColumnsConfig)
    optimization: OptimizationConfig = Field(default_factory=OptimizationConfig)

    @model_validator(mode="after")
    def validate_cross_fields(self) -> AppConfig:
        if len(self.optimization.rank_weights) != len(self.columns.choice_columns):
            raise ValueError(
                f"rank_weights length ({len(self.optimization.rank_weights)}) must match "
                f"choice_columns length ({len(self.columns.choice_columns)})."
            )
        if self.optimization.allow_unranked and self.optimization.unranked_penalty >= min(
            self.optimization.rank_weights
        ):
            raise ValueError(
                f"unranked_penalty ({self.optimization.unranked_penalty}) must be strictly "
                f"less than min(rank_weights) ({min(self.optimization.rank_weights)})."
            )
        return self


class ClassInfo(BaseModel):
    """Validated class capacity specification."""

    class_id: str
    max_capacity: int
    min_capacity: int = 0
    time_slot: str | None = None

    @model_validator(mode="after")
    def validate_capacities(self) -> ClassInfo:
        if self.min_capacity < 0:
            raise ValueError(f"Class '{self.class_id}' has negative min_capacity.")
        if self.max_capacity < 0:
            raise ValueError(f"Class '{self.class_id}' has negative max_capacity.")
        if self.min_capacity > self.max_capacity:
            raise ValueError(
                f"Class '{self.class_id}' has min_capacity ({self.min_capacity}) > "
                f"max_capacity ({self.max_capacity})."
            )
        return self


class StudentPreference(BaseModel):
    """Validated student preference ranking."""

    student_id: str
    choices: list[str]
    eligible_classes: list[str] | None = None


class AssignmentRecord(BaseModel):
    """Single student's optimal assignment result."""

    student_id: str
    assigned_class: str
    assigned_rank: int | None
    preference_score: float
    choices: list[str]


class ClassSummary(BaseModel):
    """Per-class utilization and demand summary."""

    class_id: str
    assigned_count: int
    min_capacity: int
    max_capacity: int
    utilization_pct: float
    first_choice_demand: int
    total_mentions: int
    is_bottleneck: bool


class SolveResult(BaseModel):
    """Full optimization result and diagnostics."""

    status: str
    solver_used: str
    solve_time_seconds: float
    total_preference_score: float
    objective_with_tie_break: float
    assignments: list[AssignmentRecord]
    rank_counts: dict[str, int]
    class_summaries: list[ClassSummary]
    warnings: list[str] = Field(default_factory=list)
