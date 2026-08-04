import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sacm.schemas.expert_benchmark import ExpertBenchmarkAssessmentV1


class ExpertBenchmarkService:
    def __init__(self, state_root: str | None = None) -> None:
        root = state_root or os.getenv("SACM_STATE_ROOT", ".sacm/state")
        self.path = Path(root).expanduser() / "expert-benchmark-assessment.json"

    def get(self) -> ExpertBenchmarkAssessmentV1 | None:
        if not self.path.exists():
            return None
        return ExpertBenchmarkAssessmentV1.model_validate_json(
            self.path.read_text(encoding="utf-8")
        )

    def save(
        self, assessment: ExpertBenchmarkAssessmentV1, actor: str
    ) -> ExpertBenchmarkAssessmentV1:
        persisted = assessment.model_copy(
            update={
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "updated_by": actor,
            }
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            dir=self.path.parent,
            prefix=".expert-benchmark-",
            suffix=".tmp",
            encoding="utf-8",
            delete=False,
        ) as output:
            output.write(persisted.model_dump_json(indent=2))
            temporary_path = Path(output.name)
        os.replace(temporary_path, self.path)
        return persisted
