from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from apps.api.routes import (
    agents,
    analytics,
    application_context,
    approvals,
    benchmarks,
    context,
    execution_plan,
    execution_plane,
    github,
    governance,
    intake,
    jira,
    memory,
    organizations,
    progress,
    repository,
    resilience,
    router,
    runs,
    supply_chain,
    tasks,
    traceability,
)
from sacm import __version__
from sacm.adapters.repository_adapter import RepositoryError, RepositoryPathError
from sacm.core.auth_service import (
    require_authenticated_actor,
    require_legacy_api_enabled,
    validate_production_configuration,
)
from sacm.core.repository_audit_service import TaskContextError
from sacm.core.tenancy_service import AuthorizationError
from sacm.infrastructure.db.session import engine


@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_production_configuration()
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    yield


app = FastAPI(title="SACM Agent Runtime", version=__version__, lifespan=lifespan)


@app.exception_handler(AuthorizationError)
def authorization_error(_: Request, exc: AuthorizationError) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(RepositoryPathError)
def repository_path_error(_: Request, exc: RepositoryPathError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"detail": str(exc), "error_code": "repository_path_invalid"},
    )


@app.exception_handler(RepositoryError)
def repository_operation_error(_: Request, exc: RepositoryError) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={"detail": str(exc), "error_code": "repository_operation_failed"},
    )


@app.exception_handler(TaskContextError)
def task_context_error(_: Request, exc: TaskContextError) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"detail": str(exc), "error_code": "task_context_not_found"},
    )

legacy_dependencies = [
    Depends(require_authenticated_actor),
    Depends(require_legacy_api_enabled),
]
authenticated_dependencies = [Depends(require_authenticated_actor)]

app.include_router(tasks.router, prefix="/tasks", tags=["tasks"], dependencies=legacy_dependencies)
app.include_router(agents.router, prefix="/agents", tags=["agents"], dependencies=legacy_dependencies)
app.include_router(memory.router, prefix="/memory", tags=["memory"], dependencies=legacy_dependencies)
app.include_router(router.router, prefix="/router", tags=["router"], dependencies=legacy_dependencies)
app.include_router(
    router.router,
    prefix="/v1/router",
    tags=["router"],
    dependencies=authenticated_dependencies,
)
app.include_router(context.router, prefix="/context", tags=["context"], dependencies=legacy_dependencies)
app.include_router(
    repository.router,
    prefix="/repository",
    tags=["repository"],
    dependencies=legacy_dependencies,
)
app.include_router(github.router, prefix="/github", tags=["github"])
app.include_router(
    intake.router,
    prefix="/v1/intake",
    tags=["intake"],
    dependencies=authenticated_dependencies,
)
app.include_router(jira.router, prefix="/v1/jira", tags=["jira"])
app.include_router(
    runs.router, prefix="/v1/runs", tags=["runs"], dependencies=authenticated_dependencies
)
app.include_router(
    approvals.router,
    prefix="/v1/approvals",
    tags=["approvals"],
    dependencies=authenticated_dependencies,
)
app.include_router(
    organizations.router,
    prefix="/v1/organizations",
    tags=["organizations"],
    dependencies=authenticated_dependencies,
)
app.include_router(
    governance.router,
    prefix="/v1/organizations",
    tags=["enterprise-governance"],
    dependencies=authenticated_dependencies,
)
app.include_router(
    application_context.router,
    prefix="/v1",
    tags=["application-context"],
    dependencies=authenticated_dependencies,
)
app.include_router(
    progress.router,
    prefix="/v1/tasks",
    tags=["workflow-progress"],
    dependencies=authenticated_dependencies,
)
app.include_router(
    execution_plan.router,
    prefix="/v1",
    tags=["execution-planning"],
    dependencies=authenticated_dependencies,
)
app.include_router(
    traceability.router,
    prefix="/v1",
    tags=["traceability"],
    dependencies=authenticated_dependencies,
)
app.include_router(
    analytics.router,
    prefix="/v1",
    tags=["analytics"],
    dependencies=authenticated_dependencies,
)
app.include_router(
    benchmarks.router,
    prefix="/v1",
    tags=["benchmarks"],
    dependencies=authenticated_dependencies,
)
app.include_router(execution_plane.router, prefix="/v1", tags=["execution-plane"])
app.include_router(
    supply_chain.router,
    prefix="/v1",
    tags=["supply-chain"],
    dependencies=authenticated_dependencies,
)
app.include_router(resilience.router, prefix="/v1", tags=["resilience"])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict[str, str]:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"status": "ready"}
