from contextlib import asynccontextmanager

from fastapi import FastAPI
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
from sacm.infrastructure.db.models import Base
from sacm.infrastructure.db.session import engine


@asynccontextmanager
async def lifespan(_: FastAPI):
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="SACM Agent Runtime", version="0.1.0", lifespan=lifespan)

app.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
app.include_router(agents.router, prefix="/agents", tags=["agents"])
app.include_router(memory.router, prefix="/memory", tags=["memory"])
app.include_router(router.router, prefix="/router", tags=["router"])
app.include_router(context.router, prefix="/context", tags=["context"])
app.include_router(repository.router, prefix="/repository", tags=["repository"])
app.include_router(github.router, prefix="/github", tags=["github"])
app.include_router(runs.router, prefix="/v1/runs", tags=["runs"])
app.include_router(approvals.router, prefix="/v1/approvals", tags=["approvals"])
app.include_router(organizations.router, prefix="/v1/organizations", tags=["organizations"])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
