from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from apps.api.routes import agents, context, memory, repository, router, tasks
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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
