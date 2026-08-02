import json

from sacm.adapters.github_adapter import GitHubAdapter
from sacm.core.draft_pull_request_service import DraftPullRequestService
from sacm.core.event_service import EventService
from sacm.infrastructure.db.models import ContextEvent, Task


def _result(returncode=0, stdout="", stderr=""):
    return {"returncode": returncode, "stdout": stdout, "stderr": stderr}


def test_github_delivery_creates_draft_pull_request(monkeypatch, tmp_path):
    adapter = GitHubAdapter(str(tmp_path))
    gh_lists = iter(
        [
            _result(stdout="[]"),
            _result(
                stdout=json.dumps(
                    [
                        {
                            "number": 12,
                            "url": "https://github.com/acme/repo/pull/12",
                            "isDraft": True,
                        }
                    ]
                )
            ),
        ]
    )
    git_calls = []
    gh_calls = []

    def fake_git(arguments):
        git_calls.append(arguments)
        responses = {
            ("branch", "--show-current"): _result(stdout="sacm/task\n"),
            ("status", "--porcelain"): _result(stdout=" M app.py\n"),
            ("rev-parse", "--verify", "--quiet", "main"): _result(stdout="base\n"),
            ("rev-list", "--count", "main..HEAD"): _result(stdout="0\n"),
            ("remote", "get-url", "origin"): _result(
                stdout="git@github.com:acme/repo.git\n"
            ),
            ("add", "--all"): _result(),
            ("commit", "--message", "SACM: task"): _result(),
            ("push", "--set-upstream", "origin", "sacm/task"): _result(),
        }
        return responses[tuple(arguments)]

    def fake_gh(arguments):
        gh_calls.append(arguments)
        if arguments[:2] == ["pr", "list"]:
            return next(gh_lists)
        assert arguments[:2] == ["pr", "create"]
        assert "--draft" in arguments
        return _result(stdout="https://github.com/acme/repo/pull/12\n")

    monkeypatch.setattr(adapter, "_git", fake_git)
    monkeypatch.setattr(adapter, "_gh", fake_gh)

    result = adapter.publish_draft_pull_request(
        "SACM: task",
        "Verified change",
        "sacm/task",
    )

    assert result == {
        "status": "delivered",
        "outcome": "created",
        "number": 12,
        "url": "https://github.com/acme/repo/pull/12",
        "draft": True,
    }
    assert ["push", "--set-upstream", "origin", "sacm/task"] in git_calls
    assert sum(call[:2] == ["pr", "create"] for call in gh_calls) == 1


def test_github_delivery_reuses_existing_open_pull_request(monkeypatch, tmp_path):
    adapter = GitHubAdapter(str(tmp_path))
    gh_calls = []

    def fake_git(arguments):
        responses = {
            ("branch", "--show-current"): _result(stdout="sacm/task\n"),
            ("status", "--porcelain"): _result(),
            ("rev-parse", "--verify", "--quiet", "main"): _result(stdout="base\n"),
            ("rev-list", "--count", "main..HEAD"): _result(stdout="2\n"),
            ("remote", "get-url", "origin"): _result(
                stdout="https://github.com/acme/repo.git\n"
            ),
            ("push", "--set-upstream", "origin", "sacm/task"): _result(),
        }
        return responses[tuple(arguments)]

    def fake_gh(arguments):
        gh_calls.append(arguments)
        return _result(
            stdout=json.dumps(
                [
                    {
                        "number": 7,
                        "url": "https://github.com/acme/repo/pull/7",
                        "isDraft": True,
                    }
                ]
            )
        )

    monkeypatch.setattr(adapter, "_git", fake_git)
    monkeypatch.setattr(adapter, "_gh", fake_gh)

    result = adapter.publish_draft_pull_request(
        "SACM: task",
        "Verified change",
        "sacm/task",
    )

    assert result["outcome"] == "reused"
    assert result["number"] == 7
    assert all(call[:2] != ["pr", "create"] for call in gh_calls)


def test_github_delivery_skips_branch_without_changes(monkeypatch, tmp_path):
    adapter = GitHubAdapter(str(tmp_path))

    def fake_git(arguments):
        responses = {
            ("branch", "--show-current"): _result(stdout="sacm/task\n"),
            ("status", "--porcelain"): _result(),
            ("rev-parse", "--verify", "--quiet", "main"): _result(stdout="base\n"),
            ("rev-list", "--count", "main..HEAD"): _result(stdout="0\n"),
        }
        return responses[tuple(arguments)]

    monkeypatch.setattr(adapter, "_git", fake_git)
    monkeypatch.setattr(
        adapter,
        "_gh",
        lambda arguments: (_ for _ in ()).throw(AssertionError(arguments)),
    )

    result = adapter.publish_draft_pull_request(
        "SACM: task",
        "Verified change",
        "sacm/task",
    )

    assert result == {"status": "skipped", "reason": "no_changes"}


def test_github_remote_detection_and_errors_do_not_leak_credentials():
    assert GitHubAdapter._is_github_remote("git@github.com:acme/repo.git")
    assert not GitHubAdapter._is_github_remote(
        "https://github.com.evil.example/acme/repo.git"
    )
    failed = GitHubAdapter._failed(
        {
            "stderr": (
                "push https://user:secret@example.test/repo "
                "with ghp_abcdefghijklmnopqrstuvwxyz"
            )
        }
    )

    assert "secret" not in failed["error"]
    assert "ghp_" not in failed["error"]


def test_draft_pull_request_service_skips_unverified_without_delivery(db):
    task = Task(
        id="task-pr",
        title="Task",
        description="Task",
        status="testing",
    )
    db.add(task)
    db.commit()
    called = False

    def github_factory(path):
        nonlocal called
        called = True
        raise AssertionError(path)

    result = DraftPullRequestService(db, github_factory=github_factory).publish(
        task.id, verified=False
    )

    assert result["status"] == "skipped"
    assert result["reason"] == "unverified"
    assert called is False


def test_draft_pull_request_url_and_number_are_persisted(db, tmp_path):
    task = Task(
        id="task-pr",
        title="Task",
        description="Task",
        status="testing",
    )
    db.add(task)
    db.commit()
    EventService(db).save(
        task.id,
        "agent_result",
        {
            "actions": [
                {
                    "type": "CODEX_EXECUTION",
                    "worktree_path": str(tmp_path),
                    "branch_name": "sacm/task-pr",
                }
            ]
        },
    )

    class DeliveredGitHub:
        def publish_draft_pull_request(self, **kwargs):
            return {
                "status": "delivered",
                "outcome": "created",
                "number": 42,
                "url": "https://github.com/acme/repo/pull/42",
                "draft": True,
            }

    service = DraftPullRequestService(
        db, github_factory=lambda path: DeliveredGitHub()
    )
    result = service.publish(task.id, verified=True)
    service.record(task.id, result)
    event = (
        db.query(ContextEvent)
        .filter_by(task_id=task.id, event_type="github_draft_pr_delivery")
        .one()
    )

    assert event.payload["number"] == 42
    assert event.payload["url"].endswith("/pull/42")
    assert event.payload["draft"] is True


def test_draft_pull_request_failure_is_recorded_without_reopening_task(db, tmp_path):
    task = Task(
        id="task-pr",
        title="Task",
        description="Task",
        status="done",
    )
    db.add(task)
    db.commit()
    EventService(db).save(
        task.id,
        "agent_result",
        {
            "actions": [
                {
                    "type": "CODEX_EXECUTION",
                    "worktree_path": str(tmp_path),
                    "branch_name": "sacm/task-pr",
                }
            ]
        },
    )

    class FailedGitHub:
        def publish_draft_pull_request(self, **kwargs):
            return {"status": "failed", "error": "authentication failed"}

    service = DraftPullRequestService(db, github_factory=lambda path: FailedGitHub())
    result = service.publish(task.id, verified=True, run_id="run-1")
    service.record(task.id, result)

    assert result["status"] == "failed"
    assert db.get(Task, task.id).status == "done"
    event = (
        db.query(ContextEvent)
        .filter_by(task_id=task.id, event_type="github_draft_pr_delivery")
        .one()
    )
    assert event.payload["error"] == "authentication failed"
    assert event.payload["verified"] is True
