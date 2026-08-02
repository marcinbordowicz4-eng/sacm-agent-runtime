import subprocess
from pathlib import Path

import pytest

from sacm.adapters.repository_adapter import (
    RepositoryAdapter,
    RepositoryOperationError,
)


@pytest.fixture
def temp_repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hello')")
    (tmp_path / "README.md").write_text("# Test Repo")
    return tmp_path


def test_list_files(temp_repo):
    adapter = RepositoryAdapter(str(temp_repo))
    files = adapter.list_files()
    assert "README.md" in files
    assert "src/main.py" in files


def test_repository_path_must_be_inside_configured_root(tmp_path, monkeypatch):
    allowed_root = tmp_path / "repositories"
    allowed_root.mkdir()
    allowed_repo = allowed_root / "allowed"
    allowed_repo.mkdir()
    blocked_repo = tmp_path / "blocked"
    blocked_repo.mkdir()
    monkeypatch.setenv("SACM_REPOSITORY_ROOT", str(allowed_root))

    assert RepositoryAdapter(str(allowed_repo)).repo_path == allowed_repo.resolve()
    with pytest.raises(ValueError, match="SACM_REPOSITORY_ROOT"):
        RepositoryAdapter(str(blocked_repo))


def test_repository_path_allows_configured_worktree_root(tmp_path, monkeypatch):
    repository_root = tmp_path / "repositories"
    repository_root.mkdir()
    worktree_root = tmp_path / "worktrees"
    worktree_root.mkdir()
    worktree = worktree_root / "sacm" / "task"
    worktree.mkdir(parents=True)
    monkeypatch.setenv("SACM_REPOSITORY_ROOT", str(repository_root))
    monkeypatch.setenv("SACM_WORKTREE_ROOT", str(worktree_root))

    assert RepositoryAdapter(str(worktree)).repo_path == worktree.resolve()


def test_repository_path_translates_host_mount_to_container_root(
    tmp_path, monkeypatch
):
    host_root = tmp_path / "host"
    container_root = tmp_path / "container"
    (host_root / "project").mkdir(parents=True)
    (container_root / "project").mkdir(parents=True)
    monkeypatch.setenv("SACM_HOST_REPOSITORY_ROOT", str(host_root))
    monkeypatch.setenv("SACM_REPOSITORY_ROOT", str(container_root))

    adapter = RepositoryAdapter(str(host_root / "project"))

    assert adapter.repo_path == (container_root / "project").resolve()


def test_read_file(temp_repo):
    adapter = RepositoryAdapter(str(temp_repo))
    content = adapter.read_file("README.md")
    assert "# Test Repo" in content


def test_write_file(temp_repo):
    adapter = RepositoryAdapter(str(temp_repo))
    adapter.write_file("new_file.txt", "new content")
    assert (temp_repo / "new_file.txt").read_text() == "new content"


@pytest.mark.parametrize("branch_name", ["../escape", "-option", "feature/../escape"])
def test_create_worktree_rejects_unsafe_branch_name(temp_repo, branch_name):
    adapter = RepositoryAdapter(str(temp_repo))

    with pytest.raises(ValueError, match="Invalid worktree branch name"):
        adapter.create_worktree(branch_name)


def test_create_worktree_is_idempotent(tmp_path, monkeypatch):
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repository,
        check=True,
    )
    (repository / "README.md").write_text("# Test")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=repository, check=True)
    worktree_root = tmp_path / "worktrees"
    monkeypatch.setenv("SACM_WORKTREE_ROOT", str(worktree_root))
    adapter = RepositoryAdapter(str(repository))

    first = adapter.create_worktree("sacm/task/workspace")
    second = adapter.create_worktree("sacm/task/workspace")

    assert second == first


def test_create_worktree_reuses_source_node_modules_without_overwriting(
    tmp_path, monkeypatch
):
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repository,
        check=True,
    )
    (repository / "README.md").write_text("# Test")
    (repository / ".gitignore").write_text("node_modules/\n")
    dependencies = repository / "node_modules"
    dependencies.mkdir()
    (dependencies / "marker").write_text("source")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=repository, check=True)
    monkeypatch.setenv("SACM_WORKTREE_ROOT", str(tmp_path / "worktrees"))

    worktree = Path(
        RepositoryAdapter(str(repository)).create_worktree("sacm/task/workspace")
    )

    assert (worktree / "node_modules").is_symlink()
    assert (worktree / "node_modules").resolve() == dependencies.resolve()

    subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree)],
        cwd=repository,
        check=True,
    )
    assert (dependencies / "marker").read_text() == "source"


def test_dependency_reuse_never_overwrites_existing_node_modules(tmp_path):
    repository = tmp_path / "repository"
    worktree = tmp_path / "worktree"
    (repository / "node_modules").mkdir(parents=True)
    (worktree / "node_modules").mkdir(parents=True)
    (worktree / "node_modules" / "marker").write_text("worktree")
    adapter = RepositoryAdapter(str(repository))

    adapter._reuse_node_modules(worktree)

    assert not (worktree / "node_modules").is_symlink()
    assert (worktree / "node_modules" / "marker").read_text() == "worktree"


def test_dependency_reuse_never_overwrites_existing_symlink(tmp_path):
    repository = tmp_path / "repository"
    worktree = tmp_path / "worktree"
    existing_dependencies = tmp_path / "existing"
    (repository / "node_modules").mkdir(parents=True)
    existing_dependencies.mkdir()
    worktree.mkdir()
    (worktree / "node_modules").symlink_to(
        existing_dependencies, target_is_directory=True
    )
    adapter = RepositoryAdapter(str(repository))

    adapter._reuse_node_modules(worktree)

    assert (worktree / "node_modules").resolve() == existing_dependencies.resolve()


def test_create_worktree_reports_missing_git(temp_repo, monkeypatch):
    def missing_git(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(
        "sacm.adapters.repository_adapter.subprocess.run", missing_git
    )

    with pytest.raises(RepositoryOperationError, match="Git executable"):
        RepositoryAdapter(str(temp_repo)).create_worktree("sacm/task/workspace")


def test_run_command_does_not_invoke_a_shell(temp_repo, monkeypatch):
    captured = {}

    class Completed:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(arguments, **kwargs):
        captured["arguments"] = arguments
        captured["kwargs"] = kwargs
        return Completed()

    monkeypatch.setattr("sacm.adapters.repository_adapter.subprocess.run", fake_run)

    result = RepositoryAdapter(str(temp_repo)).run_command("echo 'safe; text'")

    assert result["returncode"] == 0
    assert captured["arguments"] == ["echo", "safe; text"]
    assert "shell" not in captured["kwargs"]
