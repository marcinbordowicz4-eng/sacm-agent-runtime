import subprocess

import pytest

from sacm.core.cognitive_commit_queue_service import CognitiveCommitQueueService
from sacm.core.cognitive_state_service import CognitiveStateService
from sacm.infrastructure.db.models import (
    CognitiveCommitJob,
    CognitiveEvent,
    CognitiveRelation,
    CognitiveSnapshot,
    Organization,
    Project,
)
from sacm.schemas.cognitive_state import (
    AgentHandoffCreateV1,
    CommitIngestV1,
    GitCommitAnalyzeV1,
)


@pytest.fixture
def project(db):
    organization = Organization(id="org-cognitive", slug="cognitive", name="Cognitive")
    project = Project(
        id="project-cognitive",
        organization_id=organization.id,
        slug="runtime",
        name="Runtime",
    )
    db.add_all([organization, project])
    db.commit()
    return project


def _commit(commit_hash: str, **overrides):
    values = {
        "commit_hash": commit_hash,
        "message": "feat(auth): introduce OAuth2\n\nREQ: REQ-184\nDECISION: D91\nAGENT: coding-agent\nSKILLS: oauth2-v2, security-v2",
        "files": ["src/auth/AuthService.py", "src/auth/TokenService.py"],
        "tests": ["tests/test_auth.py"],
        "dependencies": [("src/auth/AuthService.py", "src/auth/TokenService.py")],
        "semantic_summary": "AuthService uses OAuth2 for the REQ-184 authentication flow.",
    }
    values.update(overrides)
    return CommitIngestV1(**values)


def test_commit_ingestion_creates_immutable_evidence_and_snapshot(db, project):
    service = CognitiveStateService(db)

    result = service.ingest_commit(project.id, _commit("a82f91c"))

    assert result["idempotent"] is False
    assert result["event"].event_type == "COMMIT_CREATED"
    assert result["snapshot"].commit_hash == "a82f91c"
    assert db.query(CognitiveEvent).count() == 2
    assert db.query(CognitiveRelation).count() == 9
    assert db.query(CognitiveSnapshot).count() == 1
    with pytest.raises(RuntimeError, match="immutable"):
        db.query(CognitiveEvent).first().event_type = "changed"
        db.commit()


def test_commit_ingestion_is_idempotent(db, project):
    service = CognitiveStateService(db)
    service.ingest_commit(project.id, _commit("a82f91c"))

    result = service.ingest_commit(project.id, _commit("a82f91c"))

    assert result["idempotent"] is True
    assert db.query(CognitiveEvent).count() == 2
    assert db.query(CognitiveRelation).count() == 9


def test_why_chain_and_impact_analysis_are_graph_grounded(db, project):
    service = CognitiveStateService(db)
    service.ingest_commit(project.id, _commit("a82f91c"))

    explanation = service.explain_file(project.id, "src/auth/AuthService.py")
    impact = service.impact_analysis(project.id, "src/auth/AuthService.py")

    assert explanation.requirements == ["REQ-184"]
    assert explanation.decisions == ["D91"]
    assert explanation.agents == ["coding-agent"]
    assert explanation.skills == ["oauth2-v2", "security-v2"]
    assert explanation.tests == ["tests/test_auth.py"]
    assert impact.affected_files == ["src/auth/TokenService.py"]
    assert impact.affected_requirements == ["REQ-184"]
    assert impact.affected_tests == ["tests/test_auth.py"]


def test_time_travel_excludes_later_commit_evidence(db, project):
    service = CognitiveStateService(db)
    service.ingest_commit(project.id, _commit("a82f91c"))
    service.ingest_commit(
        project.id,
        _commit(
            "b92f01d",
            message="feat(auth): OIDC\n\nREQ: REQ-191\nDECISION: D102",
            files=["src/auth/OidcService.py"],
            tests=["tests/test_oidc.py"],
            dependencies=[],
        ),
    )

    historical = service.state_at_commit(project.id, "a82f91c")
    current = service.state_at_commit(project.id, "b92f01d")
    context = service.reconstruct_context(
        project.id,
        agent_id="coding-agent",
        task="Modify OAuth2 authentication",
        commit_hash="a82f91c",
    )

    assert historical["requirements"] == ["REQ-184"]
    assert historical["decisions"] == ["D91"]
    assert "src/auth/OidcService.py" not in historical["files"]
    assert current["requirements"] == ["REQ-184", "REQ-191"]
    assert current["decisions"] == ["D102", "D91"]
    assert "src/auth/OidcService.py" not in context.code


def test_context_is_bounded_and_never_returns_secret_text(db, project):
    service = CognitiveStateService(db)
    service.ingest_commit(
        project.id,
        _commit(
            "a82f91c",
            semantic_summary="OAuth2 configuration token sk-abcdefghijklmnopqrstuvwxyz is stored elsewhere.",
        ),
    )

    context = service.reconstruct_context(
        project.id,
        agent_id="coding-agent",
        task="Modify OAuth2 authentication",
        commit_hash="a82f91c",
        max_items=5,
    )

    assert context.snapshot is not None
    assert len(context.memories) == 1
    assert "sk-" not in context.memories[0]["content"]
    assert "[REDACTED]" in context.memories[0]["content"]


def test_git_native_ingestion_collects_commit_metadata(db, project, tmp_path):
    def git(*arguments: str) -> str:
        return subprocess.run(
            ["git", *arguments],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-q")
    git("config", "user.name", "SACM test")
    git("config", "user.email", "sacm@example.test")
    source = tmp_path / "src" / "auth"
    tests = tmp_path / "tests"
    source.mkdir(parents=True)
    tests.mkdir()
    (source / "AuthService.py").write_text("class AuthService: pass\n")
    (tests / "test_auth.py").write_text("def test_auth(): pass\n")
    git("add", ".")
    git(
        "commit",
        "-qm",
        "feat(auth): OAuth2\n\nREQ: REQ-184\nDECISION: D91\nAGENT: coding-agent",
    )
    commit_hash = git("rev-parse", "HEAD")
    project.repository_path = str(tmp_path)
    db.commit()

    result = CognitiveStateService(db).analyze_and_ingest_git_commit(
        project.id,
        GitCommitAnalyzeV1(commit_hash=commit_hash, trace_id="trace-git"),
    )

    assert result["event"].commit_hash == commit_hash
    assert result["event"].agent_id == "coding-agent"
    assert result["snapshot"].state["requirements"] == ["REQ-184"]
    assert result["snapshot"].state["files"] == [
        "src/auth/AuthService.py",
        "tests/test_auth.py",
    ]
    assert result["snapshot"].state["tests"] == ["tests/test_auth.py"]


def test_handoff_is_structured_and_reconstructable(db, project):
    service = CognitiveStateService(db)
    ingested = service.ingest_commit(project.id, _commit("a82f91c"))

    handoff = service.handoff(
        project.id,
        AgentHandoffCreateV1(
            from_agent="architect-agent",
            to_agent="coding-agent",
            snapshot_id=ingested["snapshot"].id,
            requirement_ids=["REQ-184"],
            decision_ids=["D91"],
            constraints=["OAuth2", "JWT"],
            context_hash="a" * 64,
        ),
    )
    context = service.reconstruct_context(
        project.id,
        agent_id="coding-agent",
        task="Implement authentication",
    )

    assert handoff.from_agent == "architect-agent"
    assert handoff.to_agent == "coding-agent"
    assert context.handoffs[0]["id"] == handoff.id
    assert context.handoffs[0]["constraints"] == ["OAuth2", "JWT"]


def test_commit_job_is_idempotent_and_processed_asynchronously(db, project, tmp_path):
    def git(*arguments: str) -> str:
        return subprocess.run(
            ["git", *arguments],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-q")
    git("config", "user.name", "SACM test")
    git("config", "user.email", "sacm@example.test")
    (tmp_path / "app.py").write_text("print('ok')\n")
    git("add", "app.py")
    git("commit", "-qm", "feat: queue cognitive commit\n\nREQ: REQ-204")
    commit_hash = git("rev-parse", "HEAD")
    project.repository_path = str(tmp_path)
    db.commit()

    queue = CognitiveCommitQueueService(db)
    first = queue.submit(project.id, GitCommitAnalyzeV1(commit_hash=commit_hash))
    duplicate = queue.submit(project.id, GitCommitAnalyzeV1(commit_hash=commit_hash))
    processed = queue.process_one()
    job = db.get(CognitiveCommitJob, first.id)

    assert duplicate.id == first.id
    assert processed is not None
    assert processed["status"] == "COMPLETED"
    assert job is not None and job.state == "COMPLETED"
