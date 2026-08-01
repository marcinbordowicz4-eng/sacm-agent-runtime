import hashlib
import json
import re
import xml.etree.ElementTree as ET
from typing import Any, Literal

from sacm.schemas.recovery import (
    DiagnosticBundleV2,
    DiagnosticEvidenceV2,
    FailureClassification,
    FailureReportV1,
)

EvidenceKind = Literal[
    "compiler",
    "test",
    "stack_trace",
    "environment",
    "contract",
    "tool",
    "requirement",
    "history",
]

_COMPILER_PATTERNS = (
    re.compile(
        r"^(?P<file>[^:\n]+\.(?:java|py|go|rs|kt)):(?P<line>\d+)(?::\d+)?: "
        r"(?P<level>error|fatal): (?P<message>.+)$",
        re.MULTILINE | re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<file>[^(]+\.(?:ts|tsx|js|jsx))\((?P<line>\d+),\d+\): "
        r"error (?P<code>TS\d+): (?P<message>.+)$",
        re.MULTILINE,
    ),
)
_MYPY_PATTERN = re.compile(
    r"^(?P<file>[^:\n]+\.py):(?P<line>\d+): error: "
    r"(?P<message>.+?)(?:\s+\[(?P<code>[^\]]+)\])?$",
    re.MULTILINE,
)
_RUFF_PATTERN = re.compile(
    r"^(?P<file>[^:\n]+\.py):(?P<line>\d+):\d+: "
    r"(?P<code>[A-Z]+\d+) (?P<message>.+)$",
    re.MULTILINE,
)
_PYTEST_PATTERN = re.compile(
    r"^FAILED (?P<test>\S+)(?: - (?P<message>.+))?$",
    re.MULTILINE,
)
_JEST_PATTERN = re.compile(r"^\s*FAIL\s+(?P<test>\S+)", re.MULTILINE)
_ENVIRONMENT_PATTERN = re.compile(
    r"(permission denied|disk full|no space left|out of memory|connection refused|"
    r"network is unreachable|temporary failure|timed out|timeout|"
    r"could not resolve host|docker daemon|resource temporarily unavailable)",
    re.IGNORECASE,
)
_API_PATTERN = re.compile(
    r"(contract mismatch|breaking change|incompatible api|signature mismatch|"
    r"no such method|unexpected keyword argument|missing required positional argument)",
    re.IGNORECASE,
)
_CONTEXT_PATTERN = re.compile(
    r"(cannot find (?:symbol|definition|module)|unknown symbol|unresolved reference|"
    r"module not found|no module named)",
    re.IGNORECASE,
)
_ARCHITECTURE_PATTERN = re.compile(
    r"(architecture|wrong layer|boundary violation|dependency cycle)",
    re.IGNORECASE,
)
_PLAN_PATTERN = re.compile(
    r"(bad plan|invalid plan|replan|plan is wrong)", re.IGNORECASE
)
_ASSUMPTION_PATTERN = re.compile(
    r"(wrong assumption|incorrect assumption|requirement misunderstood)",
    re.IGNORECASE,
)
_MODEL_STUCK_PATTERN = re.compile(
    r"(model stuck|repeated patch|no progress|loop detected|context window)",
    re.IGNORECASE,
)
_TOOL_PATTERN = re.compile(
    r"(tool error|tool failure|tool failed|command failed|process exited)",
    re.IGNORECASE,
)


class DiagnosticService:
    """Normalizes tool output and produces evidence-backed failure diagnoses."""

    def diagnose(self, failure: dict[str, Any]) -> FailureReportV1:
        if (
            failure.get("schema_version") == "failure-report/v1"
            and failure.get("diagnosis_fingerprint")
        ):
            return FailureReportV1.model_validate(failure)
        bundle = self._bundle(failure)
        text = self._searchable_text(failure, bundle)
        classification, reason_code, confidence = self._classify(bundle, text)
        explicit = failure.get("classification")
        if explicit:
            classification = FailureClassification(str(explicit))
            reason_code = "EXPLICIT_CLASSIFICATION"
            confidence = float(failure.get("confidence") or 1.0)
        evidence = self._merge_evidence(
            self._legacy_evidence(failure),
            self._evidence(bundle),
        )
        if not evidence:
            evidence = [
                DiagnosticEvidenceV2(
                    kind=self._evidence_kind(classification),
                    source=bundle.tool or str(failure.get("type") or "runtime"),
                    message=self._root_cause(classification, bundle, text),
                )
            ]
        confidence = self._calibrate(
            confidence,
            evidence_count=len(evidence),
            has_structured_bundle=failure.get("diagnostic_bundle") is not None,
        )
        root_cause = self._root_cause(classification, bundle, text)
        fingerprint = self._fingerprint(classification, root_cause, bundle)
        reason_codes = [reason_code]
        if bundle.changed_symbols:
            reason_codes.append("GRAPH_SYMBOL_CORRELATION")
        if bundle.affected_requirements:
            reason_codes.append("GRAPH_REQUIREMENT_CORRELATION")
        if bundle.root_cause_analysis:
            reason_codes.append("MODEL_ROOT_CAUSE")
        known = {
            "schema_version",
            "classification",
            "type",
            "message",
            "evidence",
            "details",
            "retryable",
            "confidence",
            "diagnostic_bundle",
        }
        details = dict(failure.get("details") or {})
        details.update(
            {key: value for key, value in failure.items() if key not in known}
        )
        details["patch_hash"] = bundle.patch_hash
        retryable = failure.get("retryable")
        return FailureReportV1(
            classification=classification,
            type=str(failure.get("type") or "AgentFailure"),
            message=str(
                failure.get("message")
                or failure.get("detail")
                or "Agent execution failed."
            ),
            evidence=[item.model_dump(mode="json") for item in evidence],
            details=details,
            retryable=True if retryable is None else bool(retryable),
            confidence=confidence,
            root_cause=root_cause,
            reason_codes=reason_codes,
            diagnosis_fingerprint=fingerprint,
            diagnostic_bundle=bundle,
            stages=[
                "DETERMINISTIC_PARSE",
                "RULE_CLASSIFICATION",
                "GRAPH_CORRELATION",
                "ROOT_CAUSE_ANALYSIS",
                "CONFIDENCE_CALIBRATION",
                "RECOVERY_POLICY",
            ],
        )

    def _bundle(self, failure: dict[str, Any]) -> DiagnosticBundleV2:
        supplied = failure.get("diagnostic_bundle")
        if supplied:
            bundle = DiagnosticBundleV2.model_validate(supplied)
        else:
            details = failure.get("details") or {}
            bundle = DiagnosticBundleV2(
                command=details.get("command"),
                exit_code=details.get("exit_code", failure.get("returncode")),
                tool=details.get("tool"),
                raw_output=details.get("raw_output")
                or failure.get("output")
                or failure.get("message"),
                changed_symbols=list(details.get("changed_symbols") or []),
                affected_requirements=list(
                    details.get("affected_requirements") or []
                ),
                graph_context=dict(details.get("graph_context") or {}),
                patch_hash=details.get("patch_hash") or failure.get("patch_hash"),
            )
        if bundle.raw_output and not self._has_parsed_evidence(bundle):
            self._parse_output(bundle)
        return bundle

    @staticmethod
    def _has_parsed_evidence(bundle: DiagnosticBundleV2) -> bool:
        return bool(
            bundle.compiler_diagnostics
            or bundle.failed_tests
            or bundle.stack_traces
            or bundle.environment_errors
        )

    def _parse_output(self, bundle: DiagnosticBundleV2) -> None:
        output = bundle.raw_output or ""
        self._parse_json_lines(bundle, output)
        self._parse_junit(bundle, output)
        for pattern in _COMPILER_PATTERNS:
            for match in pattern.finditer(output):
                bundle.compiler_diagnostics.append(
                    self._match_evidence("compiler", bundle.tool, match)
                )
        for pattern in (_MYPY_PATTERN, _RUFF_PATTERN):
            for match in pattern.finditer(output):
                bundle.compiler_diagnostics.append(
                    self._match_evidence("compiler", bundle.tool, match)
                )
        for match in _PYTEST_PATTERN.finditer(output):
            bundle.failed_tests.append(
                DiagnosticEvidenceV2(
                    kind="test",
                    source=bundle.tool or "pytest",
                    message=match.group("message") or "Test failed.",
                    test_name=match.group("test"),
                )
            )
        for match in _JEST_PATTERN.finditer(output):
            bundle.failed_tests.append(
                DiagnosticEvidenceV2(
                    kind="test",
                    source=bundle.tool or "jest",
                    message="Test suite failed.",
                    test_name=match.group("test"),
                )
            )
        for match in _ENVIRONMENT_PATTERN.finditer(output):
            bundle.environment_errors.append(
                DiagnosticEvidenceV2(
                    kind="environment",
                    source=bundle.tool or "runtime",
                    message=match.group(0),
                )
            )

    @staticmethod
    def _match_evidence(
        kind: Literal["compiler"],
        tool: str | None,
        match,
    ) -> DiagnosticEvidenceV2:
        values = match.groupdict()
        return DiagnosticEvidenceV2(
            kind=kind,
            source=tool or "compiler",
            message=values.get("message") or match.group(0),
            code=values.get("code"),
            file=values.get("file"),
            line=int(values["line"]) if values.get("line") else None,
        )

    @staticmethod
    def _parse_json_lines(bundle: DiagnosticBundleV2, output: str) -> None:
        for line in output.splitlines():
            if not line.lstrip().startswith("{"):
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("Action") == "fail" and item.get("Test"):
                bundle.failed_tests.append(
                    DiagnosticEvidenceV2(
                        kind="test",
                        source=bundle.tool or "go test",
                        message=str(item.get("Output") or "Go test failed.").strip(),
                        test_name=str(item["Test"]),
                    )
                )
            tests = item.get("tests")
            if isinstance(tests, list):
                for test in tests:
                    if isinstance(test, dict) and test.get("outcome") in {
                        "failed",
                        "error",
                    }:
                        bundle.failed_tests.append(
                            DiagnosticEvidenceV2(
                                kind="test",
                                source=bundle.tool or "test-json",
                                message=str(test.get("message") or "Test failed."),
                                test_name=str(
                                    test.get("nodeid")
                                    or test.get("name")
                                    or "unknown"
                                ),
                            )
                        )

    @staticmethod
    def _parse_junit(bundle: DiagnosticBundleV2, output: str) -> None:
        if "<testsuite" not in output and "<testsuites" not in output:
            return
        try:
            root = ET.fromstring(output)
        except ET.ParseError:
            return
        for case in root.iter("testcase"):
            failure = case.find("failure")
            if failure is None:
                failure = case.find("error")
            if failure is not None:
                bundle.failed_tests.append(
                    DiagnosticEvidenceV2(
                        kind="test",
                        source=bundle.tool or "junit",
                        message=str(
                            failure.get("message")
                            or failure.text
                            or "Test failed."
                        ).strip(),
                        test_name=str(case.get("name") or "unknown"),
                    )
                )

    @staticmethod
    def _searchable_text(
        failure: dict[str, Any], bundle: DiagnosticBundleV2
    ) -> str:
        return "\n".join(
            filter(
                None,
                [
                    str(failure.get("type") or ""),
                    str(failure.get("message") or ""),
                    bundle.raw_output or "",
                    json.dumps(bundle.graph_context, sort_keys=True),
                ],
            )
        )

    @staticmethod
    def _classify(
        bundle: DiagnosticBundleV2, text: str
    ) -> tuple[FailureClassification, str, float]:
        if bundle.environment_errors or _ENVIRONMENT_PATTERN.search(text):
            return FailureClassification.ENVIRONMENT, "ENVIRONMENT_SIGNAL", 0.98
        if bundle.failed_tests:
            return FailureClassification.TEST_REGRESSION, "FAILED_TEST_SIGNAL", 0.97
        if bundle.compiler_diagnostics:
            return FailureClassification.COMPILATION, "COMPILER_SIGNAL", 0.98
        tool = (bundle.tool or "").lower()
        if any(
            name in tool
            for name in (
                "pytest",
                "jest",
                "vitest",
                "playwright",
                "cypress",
                "surefire",
                "go test",
            )
        ) and re.search(r"(fail|error)", text, re.IGNORECASE):
            return FailureClassification.TEST_REGRESSION, "TEST_TOOL_SIGNAL", 0.9
        if any(
            name in tool
            for name in (
                "javac",
                "maven",
                "gradle",
                "mypy",
                "ruff",
                "tsc",
                "eslint",
                "vet",
                "staticcheck",
                "terraform",
                "helm",
                "kubeconform",
            )
        ):
            return FailureClassification.COMPILATION, "VALIDATION_TOOL_SIGNAL", 0.9
        rules = (
            (_API_PATTERN, FailureClassification.API_INCOMPATIBILITY, "API_SIGNAL"),
            (_CONTEXT_PATTERN, FailureClassification.MISSING_CONTEXT, "CONTEXT_SIGNAL"),
            (
                _ARCHITECTURE_PATTERN,
                FailureClassification.ARCHITECTURE_MISMATCH,
                "ARCHITECTURE_SIGNAL",
            ),
            (_PLAN_PATTERN, FailureClassification.BAD_PLAN, "PLAN_SIGNAL"),
            (
                _ASSUMPTION_PATTERN,
                FailureClassification.WRONG_ASSUMPTION,
                "ASSUMPTION_SIGNAL",
            ),
            (
                _MODEL_STUCK_PATTERN,
                FailureClassification.MODEL_STUCK,
                "MODEL_STUCK_SIGNAL",
            ),
        )
        for pattern, classification, reason in rules:
            if pattern.search(text):
                return classification, reason, 0.88
        if _TOOL_PATTERN.search(text):
            return FailureClassification.TOOL_FAILURE, "TOOL_SIGNAL", 0.75
        if re.search(
            r"(compile|compiler|syntaxerror|type error|typeerror|build failed)",
            text,
            re.IGNORECASE,
        ):
            return FailureClassification.COMPILATION, "COMPILER_TEXT_SIGNAL", 0.86
        if re.search(
            r"(tests? failed|assertionerror|regression|pytest|jest|junit)",
            text,
            re.IGNORECASE,
        ):
            return FailureClassification.TEST_REGRESSION, "TEST_TEXT_SIGNAL", 0.86
        return FailureClassification.TOOL_FAILURE, "UNCLASSIFIED_TOOL_FAILURE", 0.4

    @staticmethod
    def _evidence_kind(
        classification: FailureClassification,
    ) -> EvidenceKind:
        kinds: dict[FailureClassification, EvidenceKind] = {
            FailureClassification.COMPILATION: "compiler",
            FailureClassification.TEST_REGRESSION: "test",
            FailureClassification.API_INCOMPATIBILITY: "contract",
            FailureClassification.ENVIRONMENT: "environment",
        }
        return kinds.get(classification, "tool")

    @staticmethod
    def _evidence(bundle: DiagnosticBundleV2) -> list[DiagnosticEvidenceV2]:
        evidence = [
            *bundle.compiler_diagnostics,
            *bundle.failed_tests,
            *bundle.stack_traces,
            *bundle.environment_errors,
        ]
        evidence.extend(
            DiagnosticEvidenceV2(
                kind="requirement",
                source="application-graph",
                message=f"Failure affects requirement {requirement}.",
                requirement_id=requirement,
            )
            for requirement in bundle.affected_requirements
        )
        return evidence

    @staticmethod
    def _legacy_evidence(failure: dict[str, Any]) -> list[DiagnosticEvidenceV2]:
        normalized: list[DiagnosticEvidenceV2] = []
        allowed_kinds = {
            "compiler",
            "test",
            "stack_trace",
            "environment",
            "contract",
            "tool",
            "requirement",
            "history",
        }
        for item in failure.get("evidence") or []:
            if not isinstance(item, dict):
                continue
            kind = item.get("kind")
            normalized.append(
                DiagnosticEvidenceV2(
                    kind=kind if kind in allowed_kinds else "tool",
                    source=str(item.get("source") or item.get("type") or "caller"),
                    message=str(
                        item.get("message")
                        or item.get("detail")
                        or json.dumps(item, sort_keys=True)
                    ),
                    code=item.get("code"),
                    file=item.get("file"),
                    line=item.get("line"),
                    test_name=item.get("test_name"),
                    requirement_id=item.get("requirement_id"),
                )
            )
        return normalized

    @staticmethod
    def _merge_evidence(
        *groups: list[DiagnosticEvidenceV2],
    ) -> list[DiagnosticEvidenceV2]:
        merged: list[DiagnosticEvidenceV2] = []
        seen: set[str] = set()
        for item in (item for group in groups for item in group):
            key = json.dumps(item.model_dump(mode="json"), sort_keys=True)
            if key not in seen:
                seen.add(key)
                merged.append(item)
        return merged

    @staticmethod
    def _calibrate(
        confidence: float,
        *,
        evidence_count: int,
        has_structured_bundle: bool,
    ) -> float:
        if evidence_count:
            confidence += min(evidence_count, 3) * 0.01
        if has_structured_bundle:
            confidence += 0.01
        return round(min(max(confidence, 0.0), 1.0), 3)

    @staticmethod
    def _root_cause(
        classification: FailureClassification,
        bundle: DiagnosticBundleV2,
        text: str,
    ) -> str:
        model_analysis = bundle.root_cause_analysis or {}
        if model_analysis.get("root_cause"):
            return str(model_analysis["root_cause"])
        evidence = DiagnosticService._evidence(bundle)
        if evidence:
            return evidence[0].message
        first_line = next(
            (
                line.strip()
                for line in (bundle.raw_output or "").splitlines()
                if line.strip()
            ),
            "",
        )
        if not first_line:
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            first_line = lines[1] if len(lines) > 1 else (lines[0] if lines else "")
        return first_line or classification.value

    @staticmethod
    def _fingerprint(
        classification: FailureClassification,
        root_cause: str,
        bundle: DiagnosticBundleV2,
    ) -> str:
        payload = {
            "classification": classification.value,
            "root_cause": root_cause,
            "command": bundle.command,
            "tool": bundle.tool,
            "changed_symbols": sorted(bundle.changed_symbols),
            "evidence": [
                {
                    "code": item.code,
                    "file": item.file,
                    "line": item.line,
                    "test_name": item.test_name,
                }
                for item in DiagnosticService._evidence(bundle)
            ],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
