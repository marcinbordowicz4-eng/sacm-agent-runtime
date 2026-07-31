import pytest
from fastapi import HTTPException

from apps.api.task_access import authorize_contract, authorize_task
from sacm.core.task_intake_service import TaskIntakeService
from sacm.core.tenancy_service import TenancyService
from sacm.schemas.task import RepositoryReference, TaskContractV1


def _project(db):
    tenancy = TenancyService(db)
    organization = tenancy.create_organization("acme", "Acme", "owner")
    project = tenancy.create_project(
        organization.id,
        "platform",
        "Platform",
        "owner",
        repository_full_name="acme/platform",
        repository_path="/repos/platform",
    )
    tenancy.add_member(organization.id, "owner", "viewer", "viewer")
    return project


def test_task_access_requires_project_membership_in_production(
    db, monkeypatch
):
    monkeypatch.setenv("SACM_ENVIRONMENT", "production")
    project = _project(db)
    contract = TaskContractV1(
        connector_type="jira",
        external_id="SACM-401",
        project_id=project.id,
        title="Protect governed task data",
        description="Enforce tenant access.",
        acceptance_criteria=["Other organizations cannot read the task."],
        repositories=[RepositoryReference(full_name="acme/platform")],
        requested_by="owner",
    )
    task, _, _ = TaskIntakeService(db).ingest(contract)

    assert authorize_task(db, task.id, "viewer").id == task.id
    with pytest.raises(HTTPException) as exc:
        authorize_task(db, task.id, "outsider")
    assert exc.value.status_code == 403


def test_production_contract_must_map_to_a_project(db, monkeypatch):
    monkeypatch.setenv("SACM_ENVIRONMENT", "production")
    contract = TaskContractV1(
        connector_type="jira",
        external_id="SACM-402",
        title="Unmapped task",
    )

    with pytest.raises(HTTPException) as exc:
        authorize_contract(db, contract, "owner")

    assert exc.value.status_code == 422
