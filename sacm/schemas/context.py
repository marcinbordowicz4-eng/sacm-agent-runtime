from pydantic import BaseModel, Field


class AgentContext(BaseModel):
    task_id: str
    task: str
    goal: str
    current_state: str
    target_repo_path: str | None = None
    relevant_memory: list[str] = Field(default_factory=list)
    files: dict[str, str] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)
    previous_findings: list[str] = Field(default_factory=list)
    test_command: str | None = None
    build_command: str | None = None


class CompileContextRequest(BaseModel):
    task_id: str
    agent_name: str
    token_budget: int = 12000


class IngestContextRequest(BaseModel):
    task_id: str
    content: str
    source_type: str
