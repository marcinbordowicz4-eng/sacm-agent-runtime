import json
import os
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from sacm.adapters.github_adapter import GitHubAdapter
from sacm.core.application_context_service import ApplicationContextService
from sacm.core.execution_plane_service import ExecutionPlaneService
from sacm.core.execution_planning_service import ExecutionPlanningService
from sacm.core.jira_service import JiraService
from sacm.core.run_service import RunService
from sacm.core.tenancy_service import ResourceAuthorizationService
from sacm.core.traceability_service import TraceabilityService
from sacm.infrastructure.db.models import (
    Artifact,
    ContextEvent,
    EvidencePack,
    ExecutionJob,
    ExecutorRegistration,
    JiraConnector,
    JiraDeliveryState,
    Run,
    Task,
)
from sacm.schemas.contracts import AgentTaskV1
from sacm.schemas.jira import JiraOrchestrationRead


class JiraOrchestrationService:
    def __init__(
        self,
        db: Session,
        *,
        jira: JiraService | None = None,
        github_factory: Callable[[str], GitHubAdapter] = GitHubAdapter,
    ) -> None:
        self.db = db
        self.jira = jira or JiraService(db)
        self.github_factory = github_factory

    def orchestrate(
        self,
        connector: JiraConnector,
        task_id: str,
        *,
        actor: str,
        policy_pack: str = "default",
        create_pull_request: bool = True,
    ) -> JiraOrchestrationRead:
        task = ResourceAuthorizationService(self.db).require_task(
            task_id, actor, "tasks.write"
        )
        if task.project_id != connector.project_id:
            raise PermissionError("Task is outside the Jira connector project.")
        readiness = task.readiness_details or {}
        if not readiness.get("ready"):
            state = self.jira.sync_status(
                connector,
                task,
                self._summary(task, None, "Clarifications are required."),
                target_state="AWAITING_CLARIFICATION",
            )
            return self._read(state, [])

        application = ApplicationContextService(self.db).build(task.id)
        plan = ExecutionPlanningService(self.db).build(
            task.id, policy_pack=policy_pack, actor_id=actor
        )
        run = self._run(task)
        jobs = self._schedule(run, plan)
        status = "EXECUTION_QUEUED" if jobs else "WAITING_FOR_EXECUTOR"
        state = self.jira.sync_status(
            connector,
            task,
            self._summary(
                task,
                run,
                (
                    f"{len(jobs)} remote execution jobs queued."
                    if jobs
                    else "No active project-scoped executor is available."
                ),
                application=application.model_dump(mode="json"),
                plan=plan.model_dump(mode="json"),
                create_pull_request=create_pull_request,
            ),
            target_state=status,
            run_id=run.id,
        )
        return self._read(state, jobs)

    def finalize(
        self,
        connector: JiraConnector,
        task_id: str,
        *,
        actor: str,
        create_pull_request: bool = True,
    ) -> JiraOrchestrationRead:
        task = ResourceAuthorizationService(self.db).require_task(
            task_id, actor, "tasks.write"
        )
        state = (
            self.db.query(JiraDeliveryState)
            .filter(JiraDeliveryState.task_id == task.id)
            .first()
        )
        if state is None or state.run_id is None:
            raise ValueError("Jira delivery has no run to finalize.")
        run = self.db.get(Run, state.run_id)
        if run is None:
            raise ValueError("Jira delivery run not found.")
        if run.status != "COMPLETED":
            state.status = "WAITING_FOR_EXECUTOR"
            state.last_error = "Run has not completed; delivery was not claimed successful."
            self.db.commit()
            return self._read(state, self._jobs(run.id))

        traceability = TraceabilityService(self.db).refresh(task.id)
        state.context = {
            **(state.context or {}),
            "traceability": traceability.model_dump(mode="json"),
        }
        if create_pull_request:
            self._deliver_pr(task, run, state)
        else:
            state.pr_status = "PR_NOT_CONFIGURED"
        state.status = "COMPLETED"
        summary = dict(state.context or {})
        summary["run"] = self._completed_run_summary(run)
        summary["pull_request"] = state.pr_url or state.pr_status
        self.jira.sync_status(
            connector,
            task,
            summary,
            target_state="COMPLETED",
            run_id=run.id,
        )
        self.db.commit()
        return self._read(state, self._jobs(run.id))

    def _run(self, task: Task) -> Run:
        existing = (
            self.db.query(Run)
            .filter(Run.task_id == task.id)
            .order_by(Run.created_at.desc())
            .first()
        )
        if existing:
            return existing
        run = Run(
            id=str(uuid.uuid4()),
            organization_id=task.organization_id,
            project_id=task.project_id,
            tenant_attribution=task.tenant_attribution,
            data_region=task.data_region,
            data_classification=task.data_classification,
            task_id=task.id,
            status="PLANNING",
            target_repo_path=task.target_repo_path,
            source_revision=self._source_revision(task),
        )
        self.db.add(run)
        self.db.flush()
        runs = RunService(self.db)
        runs._append_event(
            run,
            event_type="JiraDeliveryRunCreated",
            actor="jira-orchestrator",
            payload={"task_id": task.id, "project_id": task.project_id},
        )
        runs._checkpoint(run, "jira_delivery_run_created")
        self.db.commit()
        self.db.refresh(run)
        return run

    def _schedule(self, run: Run, plan: Any) -> list[ExecutionJob]:
        active = (
            self.db.query(ExecutorRegistration)
            .filter(
                ExecutorRegistration.project_id == run.project_id,
                ExecutorRegistration.status == "ACTIVE",
            )
            .first()
        )
        if active is None:
            return []
        runs = RunService(self.db)
        plane = ExecutionPlaneService(self.db)
        jobs: list[ExecutionJob] = []
        for planned in plan.steps:
            step = runs.add_step(
                run.id,
                planned.title,
                {
                    "execution_plan_id": plan.id,
                    "execution_plan_step_id": planned.id,
                },
                f"jira-plan:{plan.id}:{planned.id}",
            )
            role = planned.agent.role
            if role not in {"reasoner", "coder", "reviewer", "tester", "security"}:
                role = "reasoner"
            task = AgentTaskV1(
                run_id=run.id,
                step_id=step.id,
                role=role,
                objective=planned.objective,
                acceptance_criteria=planned.acceptance_criteria,
                context_references=planned.context_references,
                allowed_tools=planned.required_tools,
                token_budget=int(planned.agent.configuration.get("token_budget", 8000)),
                timeout_seconds=int(
                    planned.agent.configuration.get("timeout_seconds", 900)
                ),
                execution_context={
                    "execution_plan_id": plan.id,
                    "execution_plan_step_id": planned.id,
                },
            )
            jobs.append(
                plane.schedule(
                    run_id=run.id,
                    run_step_id=step.id,
                    task=task,
                    idempotency_key=f"jira-job:{plan.id}:{planned.id}",
                    required_capabilities=[role],
                )
            )
        if jobs:
            run.status = "IMPLEMENTING"
            self.db.commit()
        return jobs

    def _deliver_pr(self, task: Task, run: Run, state: JiraDeliveryState) -> None:
        try:
            configuration = json.loads(os.getenv("SACM_JIRA_GITHUB_PR_JSON", "{}"))
        except json.JSONDecodeError:
            configuration = {}
        project_config = configuration.get(task.project_id) if isinstance(configuration, dict) else None
        if not isinstance(project_config, dict):
            state.pr_status = "PR_NOT_CONFIGURED"
            return
        repo_path = project_config.get("repo_path") or run.target_repo_path
        branch = project_config.get("branch")
        base = project_config.get("base", "main")
        if not all(isinstance(value, str) and value for value in (repo_path, branch, base)):
            state.pr_status = "PR_NOT_CONFIGURED"
            return
        assert isinstance(repo_path, str)
        assert isinstance(branch, str)
        assert isinstance(base, str)
        result = self.github_factory(repo_path).open_pull_request(
            task.title,
            f"SACM delivery for Jira {task.external_id}\n\nRun: {run.id}",
            branch,
            base,
            draft=True,
        )
        if result.get("returncode") != 0:
            state.pr_status = "PR_FAILED"
            state.last_error = str(result.get("stderr") or "GitHub PR creation failed.")[
                :2000
            ]
            return
        state.pr_status = "DRAFT_PR_CREATED"
        state.pr_url = str(result.get("stdout") or "").strip() or None

    @staticmethod
    def _source_revision(task: Task) -> str | None:
        contract = task.task_contract or {}
        repositories = contract.get("repositories", [])
        if repositories and isinstance(repositories[0], dict):
            value = repositories[0].get("base_revision")
            return str(value) if value else None
        return None

    @staticmethod
    def _summary(
        task: Task,
        run: Run | None,
        run_text: str,
        *,
        application: dict[str, Any] | None = None,
        plan: dict[str, Any] | None = None,
        create_pull_request: bool = False,
        pull_request: str | None = None,
    ) -> dict[str, Any]:
        missing = (task.readiness_details or {}).get("missing_fields", [])
        impact = application.get("impact_analysis", {}) if application else {}
        risk = application.get("risk_analysis", {}) if application else {}
        policy = plan.get("policy_decision", {}) if plan else {}
        return {
            "questions": ", ".join(missing) or "none",
            "impact_risk": (
                f"{impact.get('impacted_repository_count', 0)} repositories; "
                f"{risk.get('level', 'unknown')} risk ({risk.get('score', 'n/a')})"
                if application
                else "not built"
            ),
            "plan": (
                f"{len(plan.get('steps', []))} steps; status {plan.get('status')}; "
                f"policy allow={policy.get('allow')}; "
                f"approvals={len(plan.get('approval_gates', []))}"
                if plan
                else "not built"
            ),
            "run": f"{run_text} Run {run.id if run else 'not created'}.",
            "pull_request": pull_request
            or ("pending completion" if create_pull_request else "PR_NOT_CONFIGURED"),
        }

    @staticmethod
    def _read(
        state: JiraDeliveryState, jobs: list[ExecutionJob]
    ) -> JiraOrchestrationRead:
        return JiraOrchestrationRead(
            task_id=state.task_id,
            run_id=state.run_id,
            status=state.status,
            pr_status=state.pr_status,
            execution_job_ids=[job.id for job in jobs],
            details=state.context,
        )

    def _jobs(self, run_id: str) -> list[ExecutionJob]:
        return (
            self.db.query(ExecutionJob)
            .filter(ExecutionJob.run_id == run_id)
            .order_by(ExecutionJob.created_at)
            .all()
        )

    def _completed_run_summary(self, run: Run) -> str:
        steps = RunService(self.db).list_steps(run.id)
        agent_count = (
            self.db.query(ContextEvent)
            .filter(
                ContextEvent.task_id == run.task_id,
                ContextEvent.event_type == "agent_result",
            )
            .count()
        )
        evidence_count = (
            self.db.query(EvidencePack)
            .filter(EvidencePack.run_id == run.id)
            .count()
        )
        test_count = (
            self.db.query(Artifact)
            .filter(
                Artifact.task_id == run.task_id,
                Artifact.artifact_type.in_(("test", "test_result", "verification")),
            )
            .count()
        )
        return (
            f"Run {run.id} {run.status}; agents={agent_count}; "
            f"steps={sum(step.status == 'COMPLETED' for step in steps)}/{len(steps)}; "
            f"tests/verifications={test_count}; evidence_packs={evidence_count}."
        )
