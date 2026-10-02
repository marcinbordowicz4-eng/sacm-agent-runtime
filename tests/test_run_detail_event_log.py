import pytest
from fastapi import HTTPException

from apps.api.routes.runs import get_event_log, list_events
from sacm.core.run_service import RunService
from sacm.core.tenancy_service import TenancyService
from sacm.schemas.run import RunCreate


def _run_id(db) -> str:
    return RunService(db).create(
        RunCreate(title="Event log", description="Run Detail event log coverage.")
    ).id


def test_event_log_is_bounded_and_returns_chronological_pages(db):
    run_id = _run_id(db)
    runs = RunService(db)
    run = runs.get(run_id)
    assert run is not None
    for number in range(1, 5):
        runs._append_event(
            run,
            event_type="AgentProgress",
            actor="agent",
            payload={"number": number},
        )
    db.commit()

    first = get_event_log(
        run_id,
        limit=2,
        before_sequence=None,
        event_type=None,
        actor="reviewer",
        db=db,
    )
    second = get_event_log(
        run_id,
        limit=2,
        before_sequence=first["next_before_sequence"],
        event_type=None,
        actor="reviewer",
        db=db,
    )

    assert [event["sequence"] for event in first["events"]] == [4, 5]
    assert first["next_before_sequence"] == 4
    assert [event["sequence"] for event in second["events"]] == [2, 3]
    assert second["next_before_sequence"] == 2
    assert first["redacted"] is True


def test_event_payload_redaction_removes_secret_material(db):
    run_id = _run_id(db)
    runs = RunService(db)
    run = runs.get(run_id)
    assert run is not None
    runs._append_event(
        run,
        event_type="ToolOutput",
        actor="agent",
        payload={"token": "super-secret-token", "message": "token=super-secret-token"},
    )
    db.commit()

    page = get_event_log(
        run_id,
        limit=20,
        before_sequence=None,
        event_type=None,
        actor="reviewer",
        db=db,
    )
    legacy = list_events(run_id, actor="reviewer", db=db)
    payload = page["events"][-1]["payload"]

    assert payload["token"] == "[REDACTED]"
    assert "super-secret-token" not in str(payload)
    assert "super-secret-token" not in str(legacy)


def test_event_log_allows_a_tenant_viewer_but_denies_non_members(db):
    tenancy = TenancyService(db)
    organization = tenancy.create_organization("acme", "Acme", "owner")
    project = tenancy.create_project(
        organization.id, "runtime", "Runtime", "owner", repository_path="/repos/runtime"
    )
    tenancy.add_member(organization.id, "owner", "reviewer", "viewer")
    run = RunService(db).create(
        RunCreate(
            title="Reviewed run",
            description="Viewer access to the timeline.",
            project_id=project.id,
            target_repo_path=project.repository_path,
        )
    )

    response = get_event_log(
        run.id,
        limit=20,
        before_sequence=None,
        event_type=None,
        actor="reviewer",
        db=db,
    )

    assert response["events"]
    with pytest.raises(HTTPException, match="Resource is not accessible"):
        get_event_log(
            run.id,
            limit=20,
            before_sequence=None,
            event_type=None,
            actor="outsider",
            db=db,
        )
