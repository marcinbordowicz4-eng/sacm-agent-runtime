from typing import Any

from pydantic import BaseModel, Field


class AgentResult(BaseModel):
    agent_name: str
    summary: str
    actions: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float
    next_state_hint: str
    memory_update: str | None = None
    # Skills proved by this agent (proof-of-state contributions).
    # Each entry is a SkillContribution dict: {skill_name, evidence, agent_name, confidence}
    skills_contributed: list[dict[str, Any]] = Field(default_factory=list)
