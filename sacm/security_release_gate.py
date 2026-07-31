from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from sacm.core.supply_chain_service import SupplyChainService

_SEVERITY = {"UNKNOWN": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


@dataclass(frozen=True)
class GateEvaluation:
    status: str
    reasons: list[str]
    report: dict[str, Any]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_policy(path: Path) -> dict[str, Any]:
    policy = json.loads(path.read_text(encoding="utf-8"))
    if policy.get("schema_version") != "release-security-policy/v1":
        raise ValueError("Unsupported release security policy contract.")
    if not policy.get("policy_version"):
        raise ValueError("Release security policy version is required.")
    if not isinstance(policy.get("required_adversarial_tests"), list):
        raise ValueError("Required adversarial tests must be a list.")
    return policy


def _load_json(path: Path, reasons: list[str]) -> dict[str, Any] | None:
    if not path.is_file():
        reasons.append(f"INCOMPLETE: missing artifact {path.name}")
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        reasons.append(f"INCOMPLETE: invalid JSON artifact {path.name}")
        return None
    if not isinstance(value, dict):
        reasons.append(f"INCOMPLETE: {path.name} must contain a JSON object")
        return None
    return value


def _results(report: dict[str, Any]) -> list[dict[str, Any]]:
    value = report.get("Results", [])
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _scan_summary(
    name: str,
    report: dict[str, Any],
    config: dict[str, Any],
    reasons: list[str],
    *,
    waived: bool,
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    ignored = 0
    if name in {"dependency", "container"}:
        blocking = {str(item).upper() for item in config["block_severities"]}
        ignore_unfixed = bool(config.get("ignore_unfixed", False))
        for result in _results(report):
            vulnerabilities = result.get("Vulnerabilities") or []
            for item in vulnerabilities if isinstance(vulnerabilities, list) else []:
                if not isinstance(item, dict):
                    continue
                severity = str(item.get("Severity", "UNKNOWN")).upper()
                if severity not in blocking:
                    continue
                if ignore_unfixed and not item.get("FixedVersion"):
                    ignored += 1
                    continue
                findings.append(
                    {
                        "id": item.get("VulnerabilityID"),
                        "package": item.get("PkgName"),
                        "severity": severity,
                        "fixed_version": item.get("FixedVersion"),
                    }
                )
    elif name == "secret":
        for result in _results(report):
            secrets = result.get("Secrets") or []
            for item in secrets if isinstance(secrets, list) else []:
                if isinstance(item, dict):
                    findings.append(
                        {
                            "id": item.get("RuleID"),
                            "target": result.get("Target"),
                            "severity": str(item.get("Severity", "UNKNOWN")).upper(),
                        }
                    )
    else:
        threshold = _SEVERITY[str(config["minimum_block_severity"]).upper()]
        for result in _results(report):
            misconfigurations = result.get("Misconfigurations") or []
            for item in misconfigurations if isinstance(misconfigurations, list) else []:
                if not isinstance(item, dict):
                    continue
                severity = str(item.get("Severity", "UNKNOWN")).upper()
                if _SEVERITY.get(severity, 0) >= threshold:
                    findings.append(
                        {
                            "id": item.get("ID"),
                            "target": result.get("Target"),
                            "severity": severity,
                        }
                    )
    if findings and not waived:
        reasons.append(f"FAIL: {name} scan has {len(findings)} blocking finding(s)")
    return {
        "blocking_findings": findings,
        "blocking_count": len(findings),
        "ignored_unfixed_count": ignored,
        "exception_applied": bool(findings and waived),
    }


def _junit_results(path: Path, reasons: list[str]) -> tuple[list[dict[str, Any]], set[str]]:
    if not path.is_file():
        reasons.append(f"INCOMPLETE: missing artifact {path.name}")
        return [], set()
    try:
        root = ElementTree.parse(path).getroot()
    except (OSError, ElementTree.ParseError):
        reasons.append(f"INCOMPLETE: invalid JUnit artifact {path.name}")
        return [], set()
    results = []
    passed: set[str] = set()
    for case in root.iter("testcase"):
        properties = {
            prop.get("name"): prop.get("value")
            for prop in case.findall("./properties/property")
        }
        identifier = properties.get("security_test_id")
        status = "passed"
        if case.find("failure") is not None or case.find("error") is not None:
            status = "failed"
        elif case.find("skipped") is not None:
            status = "skipped"
        if identifier:
            results.append(
                {
                    "id": identifier,
                    "nodeid": f"{case.get('classname')}::{case.get('name')}",
                    "status": status,
                }
            )
            if status == "passed":
                passed.add(identifier)
    return sorted(results, key=lambda item: item["id"]), passed


def evaluate_gate(
    *,
    policy_path: Path,
    artifacts: Path,
    junit_path: Path,
    git_sha: str,
) -> GateEvaluation:
    policy = load_policy(policy_path)
    reasons: list[str] = []
    now = datetime.now(timezone.utc)
    exceptions = []
    waived_controls: set[str] = set()
    valid_controls = {f"scan.{name}" for name in policy["scans"]}
    for item in policy.get("exceptions", []):
        owner = str(item.get("owner", "")).strip()
        exception_reason = str(item.get("reason", "")).strip()
        control = str(item.get("control", "")).strip()
        try:
            expires = datetime.fromisoformat(str(item["expires_at"]).replace("Z", "+00:00"))
        except (KeyError, ValueError):
            reasons.append("FAIL: policy exception has an invalid expiry")
            continue
        if (
            not owner
            or not exception_reason
            or expires <= now
            or control not in valid_controls
        ):
            reasons.append(
                "FAIL: policy exception is expired, unsupported, or lacks owner/reason"
            )
        else:
            waived_controls.add(control)
        exceptions.append(item)
    hashes: dict[str, str] = {policy_path.name: sha256_file(policy_path)}
    summaries: dict[str, Any] = {}
    for name, config in policy["scans"].items():
        path = artifacts / config["report"]
        report = _load_json(path, reasons)
        if report is not None:
            hashes[path.name] = sha256_file(path)
            if "SchemaVersion" not in report or not isinstance(
                report.get("Results"), list
            ):
                reasons.append(f"INCOMPLETE: malformed Trivy report {path.name}")
            else:
                summaries[name] = _scan_summary(
                    name,
                    report,
                    config,
                    reasons,
                    waived=f"scan.{name}" in waived_controls,
                )

    evidence = policy["evidence"]
    sbom_path = artifacts / evidence["sbom"]
    sbom = _load_json(sbom_path, reasons)
    sbom_hash = None
    if sbom is not None:
        if (
            not str(sbom.get("spdxVersion", "")).startswith("SPDX-")
            or sbom.get("SPDXID") != "SPDXRef-DOCUMENT"
            or not isinstance(sbom.get("packages"), list)
        ):
            reasons.append("FAIL: SBOM is not a valid SPDX JSON document")
        sbom_hash = sha256_file(sbom_path)
        hashes[sbom_path.name] = sbom_hash

    provenance_path = artifacts / evidence["provenance"]
    provenance = _load_json(provenance_path, reasons)
    provenance_verification: dict[str, Any] = {"status": "INCOMPLETE"}
    if provenance is not None:
        hashes[provenance_path.name] = sha256_file(provenance_path)
        verification = SupplyChainService.verify_signed_statement(provenance)
        provenance_verification = verification.model_dump(mode="json")
        if verification.status != "VALID":
            reasons.append("FAIL: provenance signature is not valid")
        if evidence.get("require_ed25519_provenance") and verification.algorithm != "ed25519":
            reasons.append("FAIL: provenance must use an Ed25519 signature")
        statement = provenance.get("statement", {})
        subjects = statement.get("subject", []) if isinstance(statement, dict) else []
        expected = {"sha256": sbom_hash}
        if sbom_hash and not any(
            isinstance(item, dict) and item.get("digest") == expected for item in subjects
        ):
            reasons.append("FAIL: provenance subject does not match the SBOM hash")
        predicate = statement.get("predicate", {}) if isinstance(statement, dict) else {}
        source = (
            predicate.get("predicate", {})
            .get("buildDefinition", {})
            .get("externalParameters", {})
            .get("source", {})
            if isinstance(predicate, dict)
            else {}
        )
        if source.get("revision") != git_sha:
            reasons.append("FAIL: provenance revision does not match the requested git SHA")

    codeql_path = artifacts / evidence["codeql"]
    codeql = _load_json(codeql_path, reasons)
    if codeql is not None:
        hashes[codeql_path.name] = sha256_file(codeql_path)
        expected = evidence["require_codeql_conclusion"]
        if codeql.get("conclusion") != expected or codeql.get("git_sha") != git_sha:
            reasons.append("FAIL: CodeQL status metadata is not successful for this git SHA")

    tool_path = artifacts / evidence["tool_versions"]
    tools = _load_json(tool_path, reasons)
    if tools is not None:
        hashes[tool_path.name] = sha256_file(tool_path)
        required_tool_fields = {"python", "docker", "trivy_action", "sbom_action"}
        if any(not tools.get(field) for field in required_tool_fields):
            reasons.append("INCOMPLETE: tool version metadata is incomplete")

    tests, passed = _junit_results(junit_path, reasons)
    if junit_path.is_file():
        hashes[junit_path.name] = sha256_file(junit_path)
    missing_tests = sorted(set(policy["required_adversarial_tests"]) - passed)
    if missing_tests:
        reasons.append(
            "FAIL: required adversarial tests did not pass: " + ", ".join(missing_tests)
        )

    incomplete = any(reason.startswith("INCOMPLETE:") for reason in reasons)
    status = "INCOMPLETE" if incomplete else "FAIL" if reasons else "PASS"
    report = {
        "schema_version": "release-security-report/v1",
        "policy_version": policy["policy_version"],
        "git_sha": git_sha,
        "generated_at": now.isoformat(),
        "status": status,
        "reasons": reasons,
        "scan_summaries": summaries,
        "adversarial_tests": tests,
        "codeql": codeql,
        "provenance_verification": provenance_verification,
        "tool_versions": tools,
        "evidence_hashes": dict(sorted(hashes.items())),
        "exceptions": exceptions,
    }
    return GateEvaluation(status=status, reasons=reasons, report=report)


def sign_report(report: dict[str, Any], private_key_file: str, key_id: str) -> dict[str, Any]:
    statement = {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [
            {
                "name": "release-security-report.json",
                "digest": {
                    "sha256": hashlib.sha256(
                        json.dumps(
                            report, sort_keys=True, separators=(",", ":")
                        ).encode()
                    ).hexdigest()
                },
            }
        ],
        "predicateType": "https://sacm.dev/release-security-report/v1",
        "predicate": report,
    }
    return SupplyChainService.sign_statement(
        statement, private_key_file=private_key_file, key_id=key_id
    )


def verify_signed_report(signed: dict[str, Any]) -> tuple[bool, list[str]]:
    result = SupplyChainService.verify_signed_statement(signed)
    errors = list(result.errors)
    statement = signed.get("statement", {})
    if statement.get("predicateType") != "https://sacm.dev/release-security-report/v1":
        errors.append("Signed artifact is not a release security report.")
    report = statement.get("predicate")
    if not isinstance(report, dict):
        errors.append("Signed release security report predicate is missing.")
    else:
        expected = hashlib.sha256(
            json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        subjects = statement.get("subject", [])
        if not any(
            isinstance(item, dict)
            and item.get("name") == "release-security-report.json"
            and item.get("digest", {}).get("sha256") == expected
            for item in subjects
        ):
            errors.append("Release security report subject hash mismatch.")
    return result.status == "VALID" and not errors, errors
