"""ContextAgent — FSM-driven meta-agent that distributes work and grows the
shared skill ledger.

Architecture
------------
The ContextAgent is selected by the main Orchestrator just like any other
agent.  Once invoked it runs its own *inner loop*:

  1. Read current FSM state from ``context.current_state``.
  2. Read accumulated proof-of-state from ``context.skill_state``.
  3. Ask the FSM for the highest-accuracy unproven transition.
  4. Enrich the worker agent's context with the skill ledger so it
     can build on previous agents' findings.
  5. Run the worker agent.
  6. Collect SkillContributions from the result.
  7. Update FSM transition accuracy via EMA (reward = confidence × progress).
  8. Repeat until no more transitions are available or max_steps reached.

Learning loop
-------------
After each sub-step the reward is fed back to ``AgentFSM.update()``.
Good transitions (high confidence, state closer to "done") push accuracy
up.  Bad transitions push it down.  Weights are persisted atomically to
``./sacm_fsm.json`` (configurable via ``SACM_FSM_PATH``) so every new
task starts with the knowledge earned by all previous tasks.
"""

from __future__ import annotations

import os
from typing import Any

from sacm.agents.architect import ArchitectAgent
from sacm.agents.backend_agent import BackendAgent
from sacm.agents.base import Agent
from sacm.agents.claude_reasoner import ClaudeReasonerAgent
from sacm.agents.cloud_executor import CloudExecutorAgent
from sacm.agents.codex_coder import CodexCoderAgent
from sacm.agents.codex_executor import CodexExecutorAgent
from sacm.agents.eas_workflow import EASWorkflowAgent
from sacm.agents.frontend_agent import FrontendAgent
from sacm.agents.github_delivery import GitHubDeliveryAgent
from sacm.agents.infrastructure_agent import InfrastructureAgent
from sacm.agents.mlflow_experiment_agent import MLflowExperimentAgent
from sacm.agents.mobile_e2e import MobileE2EAgent
from sacm.agents.openai_agents_executor import OpenAIAgentsExecutorAgent
from sacm.agents.otel_cost_agent import OpenTelemetryCostAgent
from sacm.agents.reviewer import ReviewerAgent
from sacm.agents.security_auditor import SecurityAuditorAgent
from sacm.agents.security_delivery import SecurityDeliveryAgent
from sacm.agents.test_generator import TestGeneratorAgent
from sacm.core.state_machine import AgentFSM
from sacm.schemas.context import AgentContext
from sacm.schemas.result import AgentResult
from sacm.schemas.skill import SkillContribution

_MAX_STEPS = int(os.getenv("SACM_CONTEXT_AGENT_MAX_STEPS", "6"))


class ContextAgent(Agent):
    """Meta-agent: coordinates other agents via a learnable finite state machine.

    Skills are proof-of-state tokens.  Each transition requires a skill not
    yet present in the shared ledger.  After every sub-step the FSM learns
    which transitions actually work by updating their accuracy from the
    observed reward.
    """

    name = "ContextAgent"
    role = "orchestration"
    CONTRIBUTES_SKILLS = ["orchestration_complete", "work_distributed"]

    def __init__(self, fsm: AgentFSM | None = None) -> None:
        self.fsm = fsm or AgentFSM()
        self._workers: dict[str, Agent] = {
            "ClaudeReasoner":      ClaudeReasonerAgent(),
            "CodexCoder":          CodexCoderAgent(),
            "CloudExecutor":       CloudExecutorAgent(),
            "Reviewer":            ReviewerAgent(),
            "TestGenerator":       TestGeneratorAgent(),
            "SecurityAuditor":     SecurityAuditorAgent(),
            "Architect":           ArchitectAgent(),
            "BackendAgent":        BackendAgent(),
            "FrontendAgent":       FrontendAgent(),
            "InfrastructureAgent": InfrastructureAgent(),
            "OpenTelemetryCost":   OpenTelemetryCostAgent(),
            "MLflowExperiment":    MLflowExperimentAgent(),
            "GitHubDelivery":      GitHubDeliveryAgent(),
            "CodexExecutor":       CodexExecutorAgent(),
            "EASWorkflow":         EASWorkflowAgent(),
            "MobileE2E":           MobileE2EAgent(),
            "SecurityDelivery":    SecurityDeliveryAgent(),
            "OpenAIAgentsExecutor": OpenAIAgentsExecutorAgent(),
        }

    # ------------------------------------------------------------------
    # Agent interface
    # ------------------------------------------------------------------

    def run(self, context: AgentContext) -> AgentResult:
        # Mutable skill ledger for this run — starts from inherited state
        skill_state: dict[str, Any] = dict(context.skill_state or {})
        current_state: str = context.current_state or "planning"

        step_results: list[AgentResult] = []
        all_skills: list[SkillContribution] = []

        for _ in range(_MAX_STEPS):
            proven = set(skill_state.keys())
            transition = self.fsm.best_transition(current_state, proven)

            if transition is None:
                break  # no more eligible transitions

            worker = self._workers.get(transition.agent_name)
            if worker is None:
                break

            # ── enrich worker context with accumulated ledger ──────────
            enriched = self._enrich(context, skill_state, current_state)

            # ── run worker ─────────────────────────────────────────────
            result = worker.run(enriched)

            # ── compute reward and update FSM ──────────────────────────
            reward = transition.reward_for(result.next_state_hint, result.confidence)
            self.fsm.update(transition.skill_name, reward)

            # ── record mandatory skill proof for this transition ───────
            proof = SkillContribution(
                skill_name=transition.skill_name,
                evidence=result.summary[:200],
                agent_name=transition.agent_name,
                confidence=result.confidence,
            )
            skill_state[proof.skill_name] = proof.model_dump()
            all_skills.append(proof)

            # ── absorb any extra skills the agent explicitly contributed
            for raw in result.skills_contributed or []:
                sc = raw if isinstance(raw, dict) else raw.model_dump()
                if sc.get("skill_name") and sc["skill_name"] not in skill_state:
                    skill_state[sc["skill_name"]] = sc
                    all_skills.append(SkillContribution(**sc))

            step_results.append(result)
            current_state = transition.to_state

            if transition.to_state == "done":
                break

        return self._aggregate(context, step_results, all_skills, current_state, skill_state)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _enrich(
        self,
        base: AgentContext,
        skill_state: dict[str, Any],
        current_state: str,
    ) -> AgentContext:
        """Build an enriched context for the next worker.

        Proven skills appear in ``previous_findings`` so workers can
        read what has already been established without modifying their
        own interface.
        """
        skill_summaries = [
            f"[PROVEN:{name}] {(v.get('evidence','') if isinstance(v, dict) else v.evidence)[:120]}"
            for name, v in skill_state.items()
        ]
        return AgentContext(
            task_id=base.task_id,
            task=base.task,
            goal=base.goal,
            current_state=current_state,
            target_repo_path=base.target_repo_path,
            relevant_memory=base.relevant_memory,
            files=base.files,
            constraints=base.constraints,
            previous_findings=base.previous_findings + skill_summaries,
            test_command=base.test_command,
            build_command=base.build_command,
            token_budget=base.token_budget,
            context_package=base.context_package,
            skill_state=skill_state,
        )

    def _aggregate(
        self,
        context: AgentContext,
        results: list[AgentResult],
        skills: list[SkillContribution],
        final_state: str,
        skill_state: dict[str, Any],
    ) -> AgentResult:
        if not results:
            return AgentResult(
                agent_name=self.name,
                summary=f"No transitions available from state '{context.current_state}'.",
                confidence=0.3,
                next_state_hint=context.current_state or "planning",
                skills_contributed=[],
            )

        avg_confidence = sum(r.confidence for r in results) / len(results)
        agents_used    = [r.agent_name for r in results]
        proven_names   = list(skill_state.keys())
        all_actions    = [a for r in results for a in r.actions]

        own_skills = [
            SkillContribution(
                skill_name="orchestration_complete",
                evidence=f"Ran {len(results)} agents: {agents_used}",
                agent_name=self.name,
                confidence=avg_confidence,
            ).model_dump(),
            SkillContribution(
                skill_name="work_distributed",
                evidence=f"Proven skill ledger: {proven_names}",
                agent_name=self.name,
                confidence=1.0,
            ).model_dump(),
        ]

        memory_parts = [r.memory_update for r in results if r.memory_update]

        return AgentResult(
            agent_name=self.name,
            summary=(
                f"Orchestrated {len(results)} agents via FSM "
                f"(state: {context.current_state or 'planning'} → {final_state}). "
                f"Agents: {agents_used}. "
                f"Proven skills: {proven_names}."
            ),
            actions=all_actions,
            artifacts=[],
            confidence=avg_confidence,
            next_state_hint=final_state,
            memory_update=" | ".join(memory_parts) if memory_parts else None,
            skills_contributed=own_skills,
        )
