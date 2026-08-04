from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class TaskExecutionAssessment:
    mode: str
    max_steps: int
    include_test_command: bool
    rationale: str

    def model_dump(self) -> dict[str, object]:
        return asdict(self)


class TaskExecutionAssessmentService:
    """Selects the smallest verification workflow justified by the task text."""

    _FOCUSED_TERMS = (
        "typecheck",
        "type check",
        "typescript",
        "tsc",
        "type error",
        "type errors",
    )
    _BROAD_TERMS = (
        "lint",
        "full suite",
        "all checks",
        "e2e",
        "end-to-end",
        "security scan",
    )

    def assess(
        self, description: str, *, requested_max_steps: int
    ) -> TaskExecutionAssessment:
        normalized = " ".join(description.lower().split())
        if requested_max_steps < 1:
            raise ValueError("requested_max_steps must be positive.")
        focused = any(term in normalized for term in self._FOCUSED_TERMS)
        broad = any(term in normalized for term in self._BROAD_TERMS)
        if focused and not broad:
            return TaskExecutionAssessment(
                mode="focused_verification",
                max_steps=min(requested_max_steps, 2),
                include_test_command=False,
                rationale=(
                    "The task requests focused type verification without an "
                    "explicit broader validation request."
                ),
            )
        return TaskExecutionAssessment(
            mode="standard",
            max_steps=requested_max_steps,
            include_test_command=True,
            rationale="The task requires the repository's standard validation scope.",
        )
