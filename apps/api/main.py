from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from sqlalchemy import text

from apps.api.routes import (
    agents,
    approvals,
    context,
    github,
    memory,
    organizations,
    repository,
    router,
    runs,
    tasks,
)
from sacm.core.auth_service import (
    require_authenticated_actor,
    require_legacy_api_enabled,
    validate_production_configuration,
)
from sacm.infrastructure.db.session import engine


@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_production_configuration()
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    yield


app = FastAPI(title="SACM Agent Runtime", version="0.1.0", lifespan=lifespan)

legacy_dependencies = [
    Depends(require_authenticated_actor),
    Depends(require_legacy_api_enabled),
]
authenticated_dependencies = [Depends(require_authenticated_actor)]

app.include_router(tasks.router, prefix="/tasks", tags=["tasks"], dependencies=legacy_dependencies)
app.include_router(agents.router, prefix="/agents", tags=["agents"], dependencies=legacy_dependencies)
app.include_router(memory.router, prefix="/memory", tags=["memory"], dependencies=legacy_dependencies)
app.include_router(router.router, prefix="/router", tags=["router"], dependencies=legacy_dependencies)
app.include_router(context.router, prefix="/context", tags=["context"], dependencies=legacy_dependencies)
app.include_router(
    repository.router,
    prefix="/repository",
    tags=["repository"],
    dependencies=legacy_dependencies,
)
app.include_router(github.router, prefix="/github", tags=["github"])
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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict[str, str]:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"status": "ready"}
