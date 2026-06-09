"""FeedbackService — closes the learning loop after each agent step.

Responsibilities
----------------
1. Compute a shaped reward from the AgentResult and episode outcome.
2. Derive an advantage signal (reward − moving baseline) to reduce
   variance in the REINFORCE gradient update.
3. Trigger an online weight update in the RouterService.
4. Persist router weights to disk every N updates (periodic, not every step).
5. Update the agent's ``quality_score`` in the database via EMA so the
   cost-aware routing formula has fresh signal over time.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from sacm.infrastructure.db.models import Agent
from sacm.schemas.result import AgentResult

if TYPE_CHECKING:
    from sacm.core.router import RouterService

# ---------------------------------------------------------------------------
# Tunable constants (also exposed as env-overridable module-level vars)
# ---------------------------------------------------------------------------

# How much each state hint is worth as a progress multiplier.
STATE_PROGRESS: dict[str, float] = {
    "done": 1.0,
    "testing": 0.85,
    "reviewing": 0.80,
    "coding": 0.70,
    "planning": 0.60,
    "debugging": 0.40,
    "blocked": 0.10,
}

# Bonus added when the whole task is marked done at episode end.
TASK_DONE_BONUS: float = 0.30

# EMA learning rate for the agent quality_score in the database.
EMA_ALPHA: float = 0.15

# EMA learning rate for the internal reward baseline.
BASELINE_ALPHA: float = 0.10

# How many FeedbackService.record() calls between checkpoint saves.
CHECKPOINT_EVERY: int = 10


class FeedbackService:
    """Connects agent results back to the router's learning machinery."""

    def __init__(self, db: Session, router_service: RouterService) -> None:
        self.db = db
        self.router_service = router_service
        # Running EMA of recent rewards — used as baseline for advantage.
        self._reward_baseline: float = 0.5
        self._update_count: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_reward(self, result: AgentResult, *, task_done: bool = False) -> float:
        """Shaped reward combining per-step progress and episode outcome.

        reward = confidence × state_progress  +  done_bonus (if applicable)

        The done_bonus rewards the router for the full episode completing
        successfully, not just individual agent confidence scores which are
        self-reported and can be miscalibrated.
        """
        progress = STATE_PROGRESS.get(result.next_state_hint, 0.5)
        step_reward = result.confidence * progress
        done_bonus = TASK_DONE_BONUS if task_done else 0.0
        return min(step_reward + done_bonus, 1.0)

    def record(
        self,
        context_vector: list[float],
        belief_state: list[float],
        selected_agent_index: int,
        agent_name: str,
        result: AgentResult,
        *,
        task_done: bool = False,
    ) -> float:
        """Record a completed agent step and update all learning signals.

        Args:
            context_vector:       The embedding used to make the routing decision.
            belief_state:         The belief state at decision time (must match
                                  the one passed to RouterService.route()).
            selected_agent_index: Index of the chosen agent in the registry.
            agent_name:           Name of the agent (used for DB quality update).
            result:               What the agent returned.
            task_done:            True when this is the final step of a finished task.

        Returns:
            The computed reward for this step.
        """
        reward = self.compute_reward(result, task_done=task_done)
        advantage = reward - self._reward_baseline

        # --- neural weight update ---
        self.router_service.update(
            context_vector=context_vector,
            belief_state=belief_state,
            selected_agent_index=selected_agent_index,
            advantage=advantage,
        )

        # --- baseline update (EMA of recent rewards) ---
        self._reward_baseline = (
            BASELINE_ALPHA * reward + (1.0 - BASELINE_ALPHA) * self._reward_baseline
        )

        # --- periodic checkpoint ---
        self._update_count += 1
        if self._update_count % CHECKPOINT_EVERY == 0:
            self.router_service.save_weights()

        # --- database quality score update ---
        self._update_agent_quality(agent_name, reward)

        return reward

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _update_agent_quality(self, agent_name: str, reward: float) -> None:
        """Move the agent's quality_score toward the observed reward via EMA.

        quality_{t+1} = α × reward  +  (1 − α) × quality_t

        This is a weak, smoothed signal — sufficient as a routing prior but
        not a substitute for hard external evaluation.
        """
        agent = self.db.query(Agent).filter(Agent.name == agent_name).first()
        if agent is not None:
            agent.quality_score = EMA_ALPHA * reward + (1.0 - EMA_ALPHA) * agent.quality_score
            agent.updated_at = datetime.utcnow()
            self.db.commit()
