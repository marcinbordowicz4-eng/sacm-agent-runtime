import pytest

from sacm.core.tenancy_service import AuthorizationError, TenancyService


def test_organization_owner_can_create_project_and_assign_developer(db):
    service = TenancyService(db)
    organization = service.create_organization("acme", "Acme", "owner-1")

    project = service.create_project(
        organization.id,
        "mobile",
        "Mobile",
        "owner-1",
        repository_path="/repos/mobile",
    )
    service.add_member(organization.id, "owner-1", "developer-1", "developer")

    assert (
        service.require_project_role(project.id, "developer-1", "developer").id
        == project.id
    )


def test_viewer_cannot_create_project_or_developer_run(db):
    service = TenancyService(db)
    organization = service.create_organization("acme", "Acme", "owner-1")
    service.add_member(organization.id, "owner-1", "viewer-1", "viewer")

    with pytest.raises(AuthorizationError):
        service.create_project(organization.id, "mobile", "Mobile", "viewer-1")
