import os

from sacm.agents.base import Agent
from sacm.schemas.context import AgentContext
from sacm.schemas.result import AgentResult


class MLflowExperimentAgent(Agent):
    """Logs reproducible router-experiment metadata without task content."""

    name = "MLflowExperiment"
    role = "router-experiment"
    CONTRIBUTES_SKILLS = ["router_experiment_assessed", "router_experiment_logged"]

    def run(self, context: AgentContext) -> AgentResult:
        if os.getenv("SACM_MLFLOW_ENABLED", "false").lower() != "true":
            summary = "MLflow experiment logging is disabled."
            return self._result(context, summary, logged=False)

        try:
            import mlflow
        except ImportError:
            summary = "MLflow is enabled but unavailable; install the mlflow optional dependency."
            return self._result(context, summary, logged=False)

        tracking_uri = os.getenv("MLFLOW_TRACKING_URI")
        if tracking_uri:
            mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_experiment(os.getenv("SACM_MLFLOW_EXPERIMENT", "sacm-router"))

        with mlflow.start_run(run_name=f"sacm-{context.task_id}"):
            mlflow.log_params(
                {
                    "task_id": context.task_id,
                    "state": context.current_state,
                    "token_budget": context.token_budget,
                }
            )
            mlflow.log_metrics(
                {
                    "relevant_memory_count": len(context.relevant_memory),
                    "previous_findings_count": len(context.previous_findings),
                }
            )

        return self._result(
            context,
            "Logged a reproducible router-experiment baseline to MLflow.",
            logged=True,
        )

    def _result(
        self, context: AgentContext, summary: str, *, logged: bool
    ) -> AgentResult:
        return AgentResult(
            agent_name=self.name,
            summary=summary,
            actions=[{"type": "MLFLOW_EXPERIMENT", "logged": logged}],
            confidence=1.0 if logged else 0.8,
            next_state_hint=context.current_state,
            memory_update=summary,
            skills_contributed=[
                {
                    "skill_name": "router_experiment_assessed",
                    "evidence": summary,
                    "agent_name": self.name,
                    "confidence": 1.0 if logged else 0.8,
                }
            ]
            + (
                [
                    {
                        "skill_name": "router_experiment_logged",
                        "evidence": summary,
                        "agent_name": self.name,
                        "confidence": 1.0,
                    }
                ]
                if logged
                else []
            ),
        )
