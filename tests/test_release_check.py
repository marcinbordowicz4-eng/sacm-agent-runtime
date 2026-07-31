import json
from pathlib import Path

from scripts.release_check import security_gate_check, workflow_pin_errors


def test_missing_security_gate_is_incomplete(tmp_path: Path) -> None:
    check = security_gate_check(tmp_path, "a" * 40)

    assert check.status == "INCOMPLETE"
    assert "missing required signed report" in check.detail


def test_non_passing_security_gate_is_not_accepted(tmp_path: Path) -> None:
    (tmp_path / "release-security-report.signed.json").write_text(
        json.dumps({"statement": {"predicate": {"status": "INCOMPLETE"}}}),
        encoding="utf-8",
    )

    check = security_gate_check(tmp_path, "a" * 40)

    assert check.status == "FAIL"


def test_workflow_actions_are_immutably_pinned() -> None:
    assert workflow_pin_errors() == []
