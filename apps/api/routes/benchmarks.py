from fastapi import APIRouter, Depends, HTTPException

from sacm.core.auth_service import require_authenticated_actor
from sacm.core.expert_benchmark_service import ExpertBenchmarkService
from sacm.schemas.expert_benchmark import ExpertBenchmarkAssessmentV1

router = APIRouter()


@router.get("/benchmarks/expert-assessment", response_model=ExpertBenchmarkAssessmentV1)
def get_expert_assessment(
    _: str = Depends(require_authenticated_actor),
) -> ExpertBenchmarkAssessmentV1:
    assessment = ExpertBenchmarkService().get()
    if assessment is None:
        raise HTTPException(status_code=404, detail="No expert benchmark assessment saved.")
    return assessment


@router.put("/benchmarks/expert-assessment", response_model=ExpertBenchmarkAssessmentV1)
def save_expert_assessment(
    assessment: ExpertBenchmarkAssessmentV1,
    actor: str = Depends(require_authenticated_actor),
) -> ExpertBenchmarkAssessmentV1:
    return ExpertBenchmarkService().save(assessment, actor)
