from pydantic import BaseModel


class SkillContribution(BaseModel):
    """Proof-of-state: evidence that an agent successfully applied a skill.

    Agents attach these to AgentResult.skills_contributed.
    The ContextAgent ingests them into the shared skill ledger
    (AgentContext.skill_state) so every subsequent FSM transition can
    see what has already been proven and avoid repeating work.
    """

    skill_name: str
    evidence: str      # what the agent did — concise human-readable proof
    agent_name: str    # who proved it
    confidence: float  # 0-1 strength of proof, used as reward for FSM update
