import subprocess

from sacm.core.bdd_traceability import BddTraceabilityService
from sacm.core.task_service import TaskService
from sacm.schemas.task import TaskCreate


def _git(repository, *arguments):
    subprocess.run(["git", *arguments], cwd=repository, check=True, capture_output=True)


def test_bdd_requirement_is_persisted_as_an_event(db):
    task = TaskService(db).create(
        TaskCreate(
            title="PAY-12",
            description="Feature: Checkout\nScenario: Pay invoice\nGiven an invoice\nWhen payment succeeds\nThen invoice is paid",
        )
    )

    requirement = BddTraceabilityService(db).register(task, "PAY-12")

    assert requirement["feature"] == "Checkout"
    assert requirement["scenarios"][0]["steps"][1]["keyword"] == "When"
    assert BddTraceabilityService(db).events.get_recent_events(task.id)[0].event_type == (
        "bdd_requirement_registered"
    )


def test_business_impact_links_bdd_to_changed_code(db, tmp_path):
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    source = tmp_path / "src"
    source.mkdir()
    payment = source / "payment.ts"
    payment.write_text("export const payInvoice = () => 'pending';\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "initial")
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    payment.write_text("export const payInvoice = () => 'paid';\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "pay invoice")
    target = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    task = TaskService(db).create(
        TaskCreate(
            title="PAY-12",
            description="Scenario: Pay invoice\nGiven src/payment.ts\nWhen payment succeeds\nThen invoice is paid",
            target_repo_path=str(tmp_path),
        )
    )

    impact = BddTraceabilityService(db).analyze_git_impact(task, base, target)

    assert impact["business_logic_affected"] is True
    assert impact["impacted_paths"] == ["src/payment.ts"]
