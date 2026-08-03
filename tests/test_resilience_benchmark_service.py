from typer.testing import CliRunner

from cli.main import app
from sacm.core.resilience_benchmark_service import ResilienceBenchmarkService


def test_resilience_benchmark_passes_all_control_plane_scenarios():
    report = ResilienceBenchmarkService().run()

    assert report["status"] == "PASS"
    assert report["passed"] == report["total"] == 6
    assert {item["name"] for item in report["scenarios"]} == {
        "concurrent_claim",
        "lease_expiry_recovery",
        "cancellation",
        "recovery_fallback",
        "safe_patch",
        "delivery_idempotency",
    }


def test_resilience_benchmark_cli_writes_report(tmp_path):
    output = tmp_path / "resilience.json"

    result = CliRunner().invoke(
        app, ["benchmark", "resilience", "--output", str(output)]
    )

    assert result.exit_code == 0
    assert output.is_file()
    assert '"status": "PASS"' in output.read_text()
