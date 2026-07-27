"""AgentFSM — Finite State Machine with learnable transition weights.

Each transition has an ``accuracy`` field updated via EMA after every
agent step.  High-accuracy transitions are preferred by ContextAgent.
The learned weights are persisted atomically to JSON so they survive
across tasks and improve with each cycle.

States
------
planning → coding → testing → reviewing → done
                ↓              ↓
            debugging       debugging
                ↓
              coding

Transitions
-----------
Each (from_state, to_state) pair is enabled by a *skill*.
The skill is "proven" when an agent executes the transition and adds
a SkillContribution to the shared skill ledger.
A from_state of "*" means the transition is available from any state.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Optional

_FSM_PATH = os.getenv("SACM_FSM_PATH", "./sacm_fsm.json")
_ALPHA     = float(os.getenv("SACM_FSM_ALPHA", "0.1"))

# Progress weights used when computing reward for a transition.
# Higher = closer to task completion.
STATE_PROGRESS: dict[str, float] = {
    "done":      1.00,
    "reviewing": 0.85,
    "testing":   0.70,
    "coding":    0.60,
    "planning":  0.50,
    "debugging": 0.35,
    "blocked":   0.05,
}


@dataclass
class Transition:
    """One edge in the FSM graph with a learnable accuracy weight."""

    from_state: str
    to_state: str
    skill_name: str   # which skill enables / proves this transition
    agent_name: str   # default agent to call for this transition
    accuracy: float = 0.5
    use_count: int = 0

    def update(self, reward: float) -> None:
        """EMA update: pull accuracy toward observed reward."""
        self.accuracy = _ALPHA * reward + (1.0 - _ALPHA) * self.accuracy
        self.use_count += 1

    def reward_for(self, result_next_state: str, confidence: float) -> float:
        """Shaped reward: confidence × how close next_state is to done."""
        progress = STATE_PROGRESS.get(result_next_state, 0.5)
        return confidence * progress

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Transition":
        return cls(**d)


# ── Default transition table (initial accuracies from domain knowledge) ────
#
# Format: (from_state, to_state, skill_name, agent_name, accuracy)
#
_DEFAULTS: list[tuple] = [
    # planning → coding
    ("planning",  "coding",    "task_analyzed",       "ClaudeReasoner",        0.75),
    ("planning",  "coding",    "architecture_ready",  "Architect",             0.60),
    ("planning",  "coding",    "api_designed",        "BackendAgent",          0.65),
    ("planning",  "coding",    "ui_designed",         "FrontendAgent",         0.65),
    ("planning",  "coding",    "infra_planned",       "InfrastructureAgent",   0.55),
    # coding → testing / reviewing
    ("coding",    "testing",   "code_implemented",    "CodexCoder",            0.80),
    ("coding",    "testing",   "implementation_executed", "CodexExecutor",        0.78),
    ("coding",    "reviewing", "patch_created",       "CodexCoder",            0.75),
    # testing → reviewing / debugging
    ("testing",   "reviewing", "tests_written",       "TestGenerator",         0.72),
    ("testing",   "reviewing", "mobile_e2e_executed", "MobileE2E",             0.70),
    ("testing",   "debugging", "tests_run",           "CloudExecutor",         0.60),
    # debugging → coding / testing
    ("debugging", "coding",    "root_cause_found",    "ClaudeReasoner",        0.70),
    ("debugging", "testing",   "fix_applied",         "CodexCoder",            0.65),
    # reviewing → done / back to coding
    ("reviewing", "done",      "review_complete",     "Reviewer",              0.85),
    ("reviewing", "coding",    "issues_found",        "Reviewer",              0.50),
    # wildcard — can fire from any state
    ("*",         "*",         "security_audited",    "SecurityAuditor",       0.70),
    ("*",         "*",         "cost_telemetry_assessed", "OpenTelemetryCost",   0.45),
    ("*",         "*",         "router_experiment_assessed", "MLflowExperiment", 0.40),
    ("reviewing", "reviewing", "github_delivery_preflighted", "GitHubDelivery", 0.65),
    ("testing",   "testing",  "eas_release_preflighted", "EASWorkflow",         0.50),
    ("reviewing", "reviewing", "security_ci_preflighted", "SecurityDelivery",   0.55),
]


class AgentFSM:
    """Finite State Machine whose transition accuracies improve each cycle.

    Usage
    -----
    fsm = AgentFSM()

    # pick the best available transition
    t = fsm.best_transition("planning", proven_skills=set())

    # after running the agent, update weights
    fsm.update(t.skill_name, reward=0.82)   # saves to disk automatically
    """

    def __init__(self, path: str | None = None) -> None:
        self._path = path or _FSM_PATH
        self.transitions: list[Transition] = self._load_or_default()

    # ------------------------------------------------------------------
    # Core query
    # ------------------------------------------------------------------

    def best_transition(
        self,
        current_state: str,
        proven_skills: set[str],
    ) -> Optional[Transition]:
        """Return the highest-accuracy unproven transition from current_state.

        Wildcard transitions ("*") are eligible from any state.
        Already-proven skills are excluded so the ContextAgent never
        re-runs an agent whose contribution is already in the ledger.
        """
        candidates = [
            t for t in self.transitions
            if t.from_state in (current_state, "*")
            and t.skill_name not in proven_skills
        ]
        return max(candidates, key=lambda t: t.accuracy) if candidates else None

    def transitions_from(self, state: str) -> list[Transition]:
        """All transitions available from a given state (including wildcards)."""
        return [t for t in self.transitions if t.from_state in (state, "*")]

    # ------------------------------------------------------------------
    # Learning
    # ------------------------------------------------------------------

    def update(self, skill_name: str, reward: float) -> None:
        """Update accuracy for every transition enabled by skill_name, then save."""
        for t in self.transitions:
            if t.skill_name == skill_name:
                t.update(reward)
        self._save()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_or_default(self) -> list[Transition]:
        if os.path.exists(self._path):
            try:
                with open(self._path) as fh:
                    data = json.load(fh)
                loaded = [Transition.from_dict(d) for d in data]
                # merge: keep loaded accuracy but add any new default transitions
                loaded_skills = {t.skill_name for t in loaded}
                for row in _DEFAULTS:
                    if row[2] not in loaded_skills:  # skill_name
                        loaded.append(Transition(*row))
                return loaded
            except Exception as exc:
                print(f"[AgentFSM] Could not load {self._path}: {exc}. Using defaults.")
        return [Transition(*row) for row in _DEFAULTS]

    def _save(self) -> None:
        tmp = self._path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump([t.to_dict() for t in self.transitions], fh, indent=2)
        os.replace(tmp, self._path)
