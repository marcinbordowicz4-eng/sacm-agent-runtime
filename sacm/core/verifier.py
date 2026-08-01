from typing import Any

from sqlalchemy.orm import Session

from sacm.core.traceability_service import TraceabilityService
from sacm.infrastructure.db.models import Artifact, ContextEvent, Task
from sacm.schemas.result import AgentResult
from sacm.schemas.verification import (
    ContractCompatibilityResultV1,
    EvidenceIntegrity,
    RegressionProofV1,
    RequirementVerificationV1,
    TestIntegrityResultV1,
    VerificationMatrixV2,
    VerificationStatus,
)


class Verifier:
    def __init__(self, db: Session | None = None) -> None:
        self.db = db

    @staticmethod
    def has_successful_verification(result: AgentResult) -> bool:
        verification_action_types = {"BUILD_RESULT", "TEST_RESULT", "VERIFICATION"}
        for action in result.actions:
            if action.get("type") in verification_action_types and (
                action.get("passed") is True or action.get("success") is True
            ):
                return True
        return any(
            artifact.get("type") == "verification" and artifact.get("passed") is True
            for artifact in result.artifacts
        )

    def evaluate(
        self,
        task: Task,
        result: AgentResult,
        *,
        run_id: str | None = None,
    ) -> VerificationMatrixV2:
        actions, executions = self._records(task.id, result, run_id=run_id)
        requirements = self._requirements(
            task,
            actions,
            executions,
            run_id=run_id,
        )
        strict = bool(requirements)
        if not strict:
            legacy_complete = (
                self.has_successful_verification(result)
                and result.next_state_hint not in {"blocked", "debugging"}
                and (
                    result.next_state_hint == "done"
                    or result.confidence >= 0.95
                )
            )
            return self._legacy_matrix(
                str(task.id),
                run_id,
                legacy_complete,
                requirements,
            )
        if run_id is None:
            return self._missing_run_matrix(str(task.id), requirements)

        artifact_hashes = self._artifact_hashes(task.id, run_id)
        build_action = self._latest(actions, "BUILD_RESULT")
        build_status = self._status(actions, executions, "BUILD_RESULT")
        build_evidence_valid = self._valid_evidence(
            self._evidence(build_action),
            artifact_hashes,
        )
        focused = self._test_action(actions, "focused")
        affected = self._test_action(actions, "affected-regression")
        regression_evidence_valid = self._valid_evidence(
            self._evidence(focused, affected),
            artifact_hashes,
        )
        regression = RegressionProofV1(
            focused_test_status=self._passed_status(focused, executions),
            failed_before_fix=bool(
                focused and self._base_failed(focused, executions)
            ),
            affected_area_status=self._passed_status(affected, executions),
            commands=self._commands(focused, affected),
            evidence=self._evidence(focused, affected),
            status=(
                "PASS"
                if focused
                and self._action_passed(focused, executions)
                and focused.get("failed_before_fix") is True
                and self._base_failed(focused, executions)
                and affected
                and self._action_passed(affected, executions)
                else "FAIL"
            ),
        )
        contract_action = self._latest(actions, "CONTRACT_COMPATIBILITY")
        contract = ContractCompatibilityResultV1(
            status=self._explicit_status(contract_action, executions),
            checks=list((contract_action or {}).get("checks") or []),
            evidence=self._evidence(contract_action),
        )
        contract_evidence_valid = self._valid_evidence(
            contract.evidence,
            artifact_hashes,
        )
        security_action = self._latest(actions, "SECURITY_RESULT")
        security_status = self._explicit_status(
            security_action,
            executions,
        )
        security_evidence_valid = self._valid_evidence(
            self._evidence(security_action),
            artifact_hashes,
        )
        integrity_action = self._latest(actions, "TEST_INTEGRITY")
        removed = list((integrity_action or {}).get("tests_removed") or [])
        weakened = list(
            (integrity_action or {}).get("weakened_assertions") or []
        )
        integrity_status: VerificationStatus = self._explicit_status(
            integrity_action,
            executions,
        )
        if removed or weakened:
            integrity_status = "FAIL"
        test_integrity = TestIntegrityResultV1(
            status=integrity_status,
            tests_removed=removed,
            weakened_assertions=weakened,
            evidence=self._evidence(integrity_action),
        )
        integrity_evidence_valid = self._valid_evidence(
            test_integrity.evidence,
            artifact_hashes,
        )
        blocking = []
        if any(item.status != "PASS" for item in requirements):
            blocking.append("Not all mandatory acceptance criteria are verified.")
        if build_status != "PASS":
            blocking.append("Build verification is not passing.")
        elif not build_evidence_valid:
            blocking.append("Build evidence is missing or invalid.")
        if regression.status != "PASS":
            blocking.append(
                "Regression proof must pass and fail on the pre-fix revision."
            )
        elif not regression_evidence_valid:
            blocking.append("Regression evidence is missing or invalid.")
        if contract.status != "PASS":
            blocking.append("API/schema compatibility is not verified.")
        elif not contract_evidence_valid:
            blocking.append("Contract compatibility evidence is invalid.")
        if security_status != "PASS":
            blocking.append("Security verification is not passing.")
        elif not security_evidence_valid:
            blocking.append("Security evidence is missing or invalid.")
        if test_integrity.status != "PASS":
            blocking.append("Tests were removed, weakened, or not integrity-checked.")
        elif not integrity_evidence_valid:
            blocking.append("Test-integrity evidence is missing or invalid.")
        technical_complete = not blocking
        blocking.append("A verified Evidence Pack is required.")
        return VerificationMatrixV2(
            task_id=str(task.id),
            run_id=run_id,
            strict=True,
            build_status=build_status,
            requirements=requirements,
            regression=regression,
            contract_compatibility=contract,
            security_status=security_status,
            test_integrity=test_integrity,
            technical_complete=technical_complete,
            evidence_complete=False,
            complete=False,
            blocking_reasons=blocking,
        )

    def is_done(self, task: Task, result: AgentResult) -> bool:
        return self.evaluate(task, result).complete

    def finalize_evidence(
        self,
        matrix: VerificationMatrixV2,
        *,
        evidence_valid: bool,
    ) -> VerificationMatrixV2:
        blockers = [
            reason
            for reason in matrix.blocking_reasons
            if reason != "A verified Evidence Pack is required."
        ]
        if not evidence_valid:
            blockers.append("Evidence Pack integrity verification failed.")
        return matrix.model_copy(
            update={
                "evidence_complete": evidence_valid,
                "complete": matrix.technical_complete and evidence_valid,
                "blocking_reasons": blockers,
            }
        )

    def _records(
        self,
        task_id: str,
        result: AgentResult,
        *,
        run_id: str | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        actions: list[dict[str, Any]] = []
        executions: list[dict[str, Any]] = []
        if self.db is not None:
            events = (
                self.db.query(ContextEvent)
                .filter(
                    ContextEvent.task_id == task_id,
                    ContextEvent.event_type == "agent_result",
                )
                .order_by(ContextEvent.created_at, ContextEvent.id)
                .all()
            )
            for event in events:
                contract = event.payload.get("agent_task_contract") or {}
                if run_id is not None and contract.get("run_id") != run_id:
                    continue
                actions.extend(
                    item
                    for item in event.payload.get("actions", [])
                    if isinstance(item, dict)
                )
                executions.extend(
                    item
                    for item in event.payload.get("tool_execution", [])
                    if isinstance(item, dict)
                )
        if not actions:
            actions.extend(result.actions)
            executions.extend(
                item
                for item in result.artifacts
                if item.get("type") == "tool_execution"
            )
        return actions, executions

    def _requirements(
        self,
        task: Task,
        actions: list[dict[str, Any]],
        executions: list[dict[str, Any]],
        *,
        run_id: str | None,
    ) -> list[RequirementVerificationV1]:
        if self.db is None:
            return []
        traceability = TraceabilityService(self.db).refresh(task.id)
        submitted = [
            action
            for action in actions
            if action.get("type") == "REQUIREMENT_VERIFICATION"
        ]
        executed_commands = {
            self._execution_command(execution)
            for execution in executions
            if execution.get("returncode") == 0
        }
        executed_tests = {
            str(test)
            for action in actions
            if action.get("type") == "TEST_RESULT"
            and self._action_passed(action, executions)
            for test in action.get("tests", [])
        }
        artifact_hashes = self._artifact_hashes(task.id, run_id)
        result = []
        for requirement in traceability.requirements:
            action = next(
                (
                    item
                    for item in reversed(submitted)
                    if item.get("requirement_id") == requirement.id
                    or TraceabilityService.normalize(
                        str(item.get("requirement_text") or "")
                    )
                    == requirement.normalized_text
                ),
                None,
            )
            implementation = list(
                (action or {}).get("implementation_references") or []
            )
            tests = list((action or {}).get("test_references") or [])
            commands = list((action or {}).get("verification_commands") or [])
            integrity = self._integrity(action)
            evidence = self._evidence(action)
            passed = bool(
                action
                and action.get("passed") is True
                and implementation
                and tests
                and commands
                and integrity == "VALID"
                and all(command in executed_commands for command in commands)
                and all(test in executed_tests for test in tests)
                and self._valid_evidence(evidence, artifact_hashes)
            )
            result.append(
                RequirementVerificationV1(
                    requirement_id=requirement.id,
                    requirement_text=requirement.text,
                    status="PASS" if passed else "MISSING",
                    implementation_references=implementation,
                    test_references=tests,
                    verification_commands=commands,
                    evidence_integrity=integrity,
                    evidence=evidence,
                )
            )
        return result

    @staticmethod
    def _legacy_matrix(
        task_id: str,
        run_id: str | None,
        complete: bool,
        requirements: list[RequirementVerificationV1],
    ) -> VerificationMatrixV2:
        status: VerificationStatus = "PASS" if complete else "MISSING"
        return VerificationMatrixV2(
            task_id=task_id,
            run_id=run_id,
            strict=False,
            build_status=status,
            requirements=requirements,
            regression=RegressionProofV1(
                focused_test_status=status,
                failed_before_fix=complete,
                affected_area_status=status,
                status=status,
            ),
            contract_compatibility=ContractCompatibilityResultV1(
                status="NOT_APPLICABLE"
            ),
            security_status="NOT_APPLICABLE",
            test_integrity=TestIntegrityResultV1(status=status),
            technical_complete=complete,
            evidence_complete=complete,
            complete=complete,
            blocking_reasons=[] if complete else ["No successful verification."],
        )

    @staticmethod
    def _latest(
        actions: list[dict[str, Any]],
        action_type: str,
    ) -> dict[str, Any] | None:
        return next(
            (
                action
                for action in reversed(actions)
                if action.get("type") == action_type
            ),
            None,
        )

    @classmethod
    def _status(
        cls,
        actions: list[dict[str, Any]],
        executions: list[dict[str, Any]],
        action_type: str,
    ) -> VerificationStatus:
        return cls._passed_status(
            cls._latest(actions, action_type),
            executions,
        )

    @staticmethod
    def _passed_status(
        action: dict[str, Any] | None,
        executions: list[dict[str, Any]],
    ) -> VerificationStatus:
        if action is None:
            return "MISSING"
        return (
            "PASS"
            if Verifier._action_passed(action, executions)
            else "FAIL"
        )

    @staticmethod
    def _integrity(action: dict[str, Any] | None) -> EvidenceIntegrity:
        integrity = str(
            (action or {}).get("evidence_integrity") or "MISSING"
        ).upper()
        if integrity == "VALID":
            return "VALID"
        if integrity == "INVALID":
            return "INVALID"
        return "MISSING"

    @staticmethod
    def _explicit_status(
        action: dict[str, Any] | None,
        executions: list[dict[str, Any]],
    ) -> VerificationStatus:
        if action is None:
            return "MISSING"
        status = str(action.get("status") or "").upper()
        if status == "PASS":
            return (
                "PASS"
                if Verifier._action_passed(action, executions)
                else "FAIL"
            )
        if status == "FAIL":
            return "FAIL"
        return (
            "PASS"
            if Verifier._action_passed(action, executions)
            else "FAIL"
        )

    @classmethod
    def _test_action(
        cls,
        actions: list[dict[str, Any]],
        scope: str,
    ) -> dict[str, Any] | None:
        return next(
            (
                action
                for action in reversed(actions)
                if action.get("type") == "TEST_RESULT"
                and action.get("scope") == scope
            ),
            None,
        )

    @staticmethod
    def _commands(*actions: dict[str, Any] | None) -> list[str]:
        return [
            str(action["command"])
            for action in actions
            if action and action.get("command")
        ]

    @staticmethod
    def _evidence(*actions: dict[str, Any] | None) -> list[dict[str, Any]]:
        return [
            item
            for action in actions
            if action
            for item in action.get("evidence", [])
            if isinstance(item, dict)
        ]

    @staticmethod
    def _action_passed(
        action: dict[str, Any],
        executions: list[dict[str, Any]],
    ) -> bool:
        command = str(action.get("command") or "")
        return bool(
            command
            and action.get("passed") is True
            and any(
                Verifier._execution_command(execution) == command
                and execution.get("returncode") == 0
                for execution in executions
            )
        )

    @staticmethod
    def _valid_evidence(
        evidence: list[dict[str, Any]],
        artifact_hashes: set[str],
    ) -> bool:
        if not evidence:
            return False
        return all(
            isinstance(item.get("sha256"), str)
            and item["sha256"].lower() in artifact_hashes
            for item in evidence
        )

    def _artifact_hashes(
        self,
        task_id: str,
        run_id: str | None,
    ) -> set[str]:
        if self.db is None or run_id is None:
            return set()
        rows = (
            self.db.query(Artifact)
            .filter(
                Artifact.task_id == task_id,
                Artifact.content_hash.is_not(None),
            )
            .all()
        )
        return {
            str(row.content_hash).lower()
            for row in rows
            if (row.metadata_ or {}).get("run_id") == run_id
        }

    @staticmethod
    def _execution_command(execution: dict[str, Any]) -> str:
        return str(execution.get("command") or execution.get("tool") or "")

    @classmethod
    def _base_failed(
        cls,
        action: dict[str, Any],
        executions: list[dict[str, Any]],
    ) -> bool:
        command = str(action.get("base_revision_command") or "")
        return bool(
            command
            and any(
                cls._execution_command(execution) == command
                and isinstance(execution.get("returncode"), int)
                and execution["returncode"] != 0
                for execution in executions
            )
        )

    @staticmethod
    def _missing_run_matrix(
        task_id: str,
        requirements: list[RequirementVerificationV1],
    ) -> VerificationMatrixV2:
        return VerificationMatrixV2(
            task_id=task_id,
            strict=True,
            build_status="MISSING",
            requirements=requirements,
            regression=RegressionProofV1(
                focused_test_status="MISSING",
                failed_before_fix=False,
                affected_area_status="MISSING",
                status="MISSING",
            ),
            contract_compatibility=ContractCompatibilityResultV1(
                status="MISSING"
            ),
            security_status="MISSING",
            test_integrity=TestIntegrityResultV1(status="MISSING"),
            technical_complete=False,
            evidence_complete=False,
            complete=False,
            blocking_reasons=[
                "Strict verification requires an explicit run identifier."
            ],
        )
