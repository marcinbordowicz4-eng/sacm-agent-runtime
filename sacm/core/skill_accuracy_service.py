"""SkillAccuracyService — persists and improves skill proofs across task cycles.

Design
------
Each skill a SkillContribution declares is stored as a ``SkillRecord`` in the
database.  After every agent step the reward signal updates the record's
``accuracy`` via exponential moving average (EMA):

    accuracy_{t+1} = α · reward_t + (1 − α) · accuracy_t

This means:
* A skill that consistently leads to high reward drifts toward 1.0.
* A skill that fails often drifts toward 0.0 and is eventually excluded.
* ``use_count`` tracks how many times each skill has been exercised.

Only skills whose accuracy exceeds ``MIN_ACCURACY`` are returned for
injection into future agent contexts, forming a positive feedback loop:
good skills get used → they get reinforced → they keep improving.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from sacm.infrastructure.db.models import SkillRecord

EMA_ALPHA: float = 0.2          # how fast accuracy reacts to new rewards
MIN_ACCURACY: float = 0.55      # threshold to be considered "proven"
MAX_SKILLS_PER_AGENT: int = 8   # cap on injected skills to avoid prompt bloat


class SkillAccuracyService:
    """Records skill outcomes and exposes proven skills for context injection."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def record_outcome(
        self,
        skill_name: str,
        agent_name: str,
        reward: float,
        evidence: str = "",
    ) -> SkillRecord:
        """Create or update a SkillRecord based on the observed reward.

        The accuracy score is updated via EMA so that each cycle either
        reinforces a good skill or gradually demotes a poor one.
        """
        record = (
            self.db.query(SkillRecord)
            .filter(SkillRecord.skill_name == skill_name, SkillRecord.agent_name == agent_name)
            .first()
        )

        if record is None:
            record = SkillRecord(
                id=str(uuid.uuid4()),
                skill_name=skill_name,
                agent_name=agent_name,
                instructions=evidence or f"{agent_name} proved: {skill_name}",
                accuracy=reward,
                use_count=1,
                last_reward=reward,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            self.db.add(record)
        else:
            record.accuracy = EMA_ALPHA * reward + (1.0 - EMA_ALPHA) * record.accuracy
            record.last_reward = reward
            record.use_count += 1
            record.instructions = evidence or record.instructions
            record.updated_at = datetime.utcnow()

        self.db.commit()
        return record

    def record_contributions(
        self,
        skills_contributed: list[dict],
        reward: float,
    ) -> None:
        """Batch-update accuracy for all skills from an AgentResult."""
        for skill in skills_contributed:
            if not isinstance(skill, dict):
                continue
            self.record_outcome(
                skill_name=skill.get("skill_name", "unknown"),
                agent_name=skill.get("agent_name", "unknown"),
                reward=reward,
                evidence=skill.get("evidence", ""),
            )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_proven_skills(
        self,
        agent_name: str | None = None,
        min_accuracy: float = MIN_ACCURACY,
    ) -> list[SkillRecord]:
        """Return skills above the accuracy threshold, best first."""
        q = self.db.query(SkillRecord).filter(SkillRecord.accuracy >= min_accuracy)
        if agent_name:
            q = q.filter(SkillRecord.agent_name == agent_name)
        return (
            q.order_by(SkillRecord.accuracy.desc())
            .limit(MAX_SKILLS_PER_AGENT)
            .all()
        )

    def get_skill_instructions(
        self,
        agent_name: str | None = None,
        min_accuracy: float = MIN_ACCURACY,
    ) -> list[str]:
        """Return injection-ready instruction strings for proven skills."""
        return [
            f"[proven skill | accuracy={s.accuracy:.2f}] {s.skill_name}: {s.instructions}"
            for s in self.get_proven_skills(agent_name, min_accuracy)
        ]

    def get_accuracy(self, skill_name: str, agent_name: str) -> float | None:
        """Return current accuracy for a specific skill, or None if unknown."""
        record = (
            self.db.query(SkillRecord)
            .filter(SkillRecord.skill_name == skill_name, SkillRecord.agent_name == agent_name)
            .first()
        )
        return record.accuracy if record else None
