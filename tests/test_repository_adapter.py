import subprocess
from pathlib import Path

import pytest

from sacm.adapters.repository_adapter import (
    RepositoryAdapter,
    RepositoryOperationError,
    RepositoryPathError,
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


def test_repository_path_translates_host_mount_to_container_root(tmp_path, monkeypatch):
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


def test_create_worktree_never_links_or_deletes_source_node_modules(
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

    assert not (worktree / "node_modules").exists()
    assert not (worktree / "node_modules").is_symlink()
    assert (dependencies / "marker").read_text() == "source"

    subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree)],
        cwd=repository,
        check=True,
    )
    assert (dependencies / "marker").read_text() == "source"


def test_worktree_setup_never_overwrites_existing_node_modules(tmp_path):
    repository = tmp_path / "repository"
    worktree = tmp_path / "worktree"
    (repository / "node_modules").mkdir(parents=True)
    (worktree / "node_modules").mkdir(parents=True)
    (worktree / "node_modules" / "marker").write_text("worktree")
    adapter = RepositoryAdapter(str(repository))

    adapter._ensure_independent_node_modules(worktree)

    assert not (worktree / "node_modules").is_symlink()
    assert (worktree / "node_modules" / "marker").read_text() == "worktree"


def test_worktree_setup_removes_legacy_shared_node_modules_symlink(tmp_path):
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

    adapter._ensure_independent_node_modules(worktree)

    assert not (worktree / "node_modules").exists()
    assert (existing_dependencies).is_dir()


def test_dependency_cache_is_reused_with_independent_worktree_dependencies(
    tmp_path, monkeypatch
):
    repository = tmp_path / "repository"
    first = tmp_path / "worktrees" / "first"
    second = tmp_path / "worktrees" / "second"
    repository.mkdir()
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    lock = '{"lockfileVersion": 3, "packages": {}}'
    (first / "package-lock.json").write_text(lock)
    (second / "package-lock.json").write_text(lock)
    cache_root = tmp_path / "dependency-cache"
    monkeypatch.setenv("SACM_DEPENDENCY_CACHE_ROOT", str(cache_root))
    adapter = RepositoryAdapter(str(repository))

    first_cache = adapter.dependency_cache(first)
    second_cache = adapter.dependency_cache(second)
    (first / "node_modules").mkdir()
    (second / "node_modules").mkdir()
    (first / "node_modules" / "marker").write_text("first")
    (second / "node_modules" / "marker").write_text("second")

    assert first_cache is not None
    assert second_cache is not None
    assert first_cache.cache_key == second_cache.cache_key
    assert first_cache.cache_path == second_cache.cache_path
    assert first_cache.environment["npm_config_cache"] == str(
        first_cache.cache_path / "downloads"
    )
    assert first_cache.install_command == [
        "npm",
        "ci",
        "--prefer-offline",
        "--no-audit",
        "--no-fund",
    ]
    assert (first / "node_modules" / "marker").read_text() == "first"
    assert (second / "node_modules" / "marker").read_text() == "second"


def test_dependency_cache_lockfile_change_invalidates_key(tmp_path, monkeypatch):
    repository = tmp_path / "repository"
    worktree = tmp_path / "worktree"
    repository.mkdir()
    worktree.mkdir()
    lockfile = worktree / "package-lock.json"
    monkeypatch.setenv("SACM_DEPENDENCY_CACHE_ROOT", str(tmp_path / "dependency-cache"))
    adapter = RepositoryAdapter(str(repository))

    lockfile.write_text('{"lockfileVersion": 3, "packages": {}}')
    first = adapter.dependency_cache(worktree)
    lockfile.write_text('{"lockfileVersion": 3, "packages": {"node_modules/x": {}}}')
    second = adapter.dependency_cache(worktree)

    assert first is not None
    assert second is not None
    assert first.cache_key != second.cache_key
    assert first.cache_path != second.cache_path


def test_node_cache_key_includes_package_manifest(tmp_path, monkeypatch):
    repository = tmp_path / "repository"
    worktree = tmp_path / "worktree"
    repository.mkdir()
    worktree.mkdir()
    (worktree / "package-lock.json").write_text('{"lockfileVersion": 3}')
    package = worktree / "package.json"
    package.write_text('{"dependencies":{"one":"1.0.0"}}')
    monkeypatch.setenv("SACM_DEPENDENCY_CACHE_ROOT", str(tmp_path / "cache"))
    adapter = RepositoryAdapter(str(repository))

    first = adapter.dependency_cache(worktree)
    package.write_text('{"dependencies":{"two":"1.0.0"}}')
    second = adapter.dependency_cache(worktree)

    assert first is not None
    assert second is not None
    assert first.cache_key != second.cache_key


def test_node_dependency_snapshot_is_ready_only_after_publish_and_isolated(
    tmp_path, monkeypatch
):
    repository = tmp_path / "repository"
    worktree = tmp_path / "worktree"
    restored = tmp_path / "restored"
    repository.mkdir()
    worktree.mkdir()
    restored.mkdir()
    (worktree / "package-lock.json").write_text('{"lockfileVersion": 3}')
    (worktree / "package.json").write_text('{"name":"example"}')
    node_modules = worktree / "node_modules"
    (node_modules / "example").mkdir(parents=True)
    (node_modules / "example" / "index.js").write_text("module.exports = 1")
    monkeypatch.setenv("SACM_DEPENDENCY_CACHE_ROOT", str(tmp_path / "cache"))
    adapter = RepositoryAdapter(str(repository))
    cache = adapter.dependency_cache(worktree)

    assert cache is not None
    cache.node_modules_path.mkdir()
    assert not adapter.restore_node_dependencies(restored, cache)

    with adapter.node_dependency_cache_lock(cache):
        adapter.publish_node_dependencies(worktree, cache)

    assert adapter.node_dependency_cache_ready(cache)
    assert adapter.restore_node_dependencies(restored, cache)
    assert not (restored / "node_modules").is_symlink()
    (restored / "node_modules" / "example" / "index.js").write_text("changed")
    assert (cache.node_modules_path / "example" / "index.js").read_text() == (
        "module.exports = 1"
    )


def test_dependency_cache_root_cannot_be_in_worktree(tmp_path, monkeypatch):
    repository = tmp_path / "repository"
    worktree = tmp_path / "worktree"
    repository.mkdir()
    worktree.mkdir()
    (worktree / "package-lock.json").write_text('{"lockfileVersion": 3}')
    monkeypatch.setenv("SACM_DEPENDENCY_CACHE_ROOT", str(worktree / ".cache"))

    with pytest.raises(RepositoryPathError, match="outside the isolated Git worktree"):
        RepositoryAdapter(str(repository)).dependency_cache(worktree)


def test_dependency_cache_root_cannot_be_in_source_git_worktree(
    tmp_path, monkeypatch
):
    repository = _git_repository(tmp_path / "repository")
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / "package-lock.json").write_text('{"lockfileVersion": 3}')
    monkeypatch.setenv("SACM_DEPENDENCY_CACHE_ROOT", str(repository / ".cache"))

    with pytest.raises(RepositoryPathError, match="outside the isolated Git worktree"):
        RepositoryAdapter(str(repository)).dependency_cache(worktree)


@pytest.mark.parametrize(
    ("manifest", "manager", "command"),
    [
        ("pom.xml", "maven", "mvn"),
        ("build.gradle", "gradle", "gradle"),
        ("uv.lock", "uv", "uv"),
        ("poetry.lock", "poetry", "poetry"),
        ("Cargo.lock", "cargo", "cargo"),
        ("go.sum", "go", "go"),
    ],
)
def test_dependency_cache_supports_non_node_manifests(
    tmp_path, monkeypatch, manifest, manager, command
):
    repository = tmp_path / "repository"
    worktree = tmp_path / "worktree"
    repository.mkdir()
    worktree.mkdir()
    (worktree / manifest).write_text("dependency-manifest")
    monkeypatch.setenv("SACM_DEPENDENCY_CACHE_ROOT", str(tmp_path / "cache"))

    cache = RepositoryAdapter(str(repository)).dependency_cache(worktree)

    assert cache is not None
    assert cache.manager == manager
    assert cache.install_command[0] == command


def test_create_worktree_reports_missing_git(temp_repo, monkeypatch):
    def missing_git(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr("sacm.adapters.repository_adapter.subprocess.run", missing_git)

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


def _git_repository(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=path, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "app.py").write_text("old\n")
    subprocess.run(["git", "add", "app.py"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=path, check=True)
    return path


def test_apply_patch_runs_preflight_and_returns_integrity_metadata(tmp_path):
    repository = _git_repository(tmp_path / "repository")
    (repository / "app.py").write_text("new\n")
    patch = subprocess.run(
        ["git", "diff"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    subprocess.run(["git", "checkout", "--", "app.py"], cwd=repository, check=True)

    result = RepositoryAdapter(str(repository)).apply_patch(patch)

    assert (repository / "app.py").read_text() == "new\n"
    assert result["changed_files"] == ["app.py"]
    assert len(result["patch_sha256"]) == 64


def test_apply_patch_rejects_protected_and_escaping_paths(tmp_path):
    repository = _git_repository(tmp_path / "repository")
    adapter = RepositoryAdapter(str(repository))

    protected = (
        "diff --git a/.env b/.env\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/.env\n"
        "@@ -0,0 +1 @@\n"
        "+SECRET=value\n"
    )
    escaping = (
        "diff --git a/../outside b/../outside\n"
        "--- a/../outside\n"
        "+++ b/../outside\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )

    with pytest.raises(RepositoryPathError, match="protected"):
        adapter.apply_patch(protected)
    with pytest.raises(RepositoryPathError, match="Unsafe patch path"):
        adapter.apply_patch(escaping)


def test_apply_patch_enforces_size_limit(tmp_path, monkeypatch):
    repository = _git_repository(tmp_path / "repository")
    monkeypatch.setenv("SACM_PATCH_MAX_BYTES", "20")

    with pytest.raises(RepositoryOperationError, match="MAX_BYTES"):
        RepositoryAdapter(str(repository)).apply_patch(
            "diff --git a/app.py b/app.py\n"
        )


def test_apply_patch_rolls_back_files_when_apply_fails(tmp_path, monkeypatch):
    repository = _git_repository(tmp_path / "repository")
    adapter = RepositoryAdapter(str(repository))
    patch = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )
    calls = 0

    def fake_apply(value, arguments):
        nonlocal calls
        calls += 1
        if calls == 1:
            return subprocess.CompletedProcess(arguments, 0, "", "")
        (repository / "app.py").write_text("partially changed\n")
        return subprocess.CompletedProcess(arguments, 1, "", "simulated failure")

    monkeypatch.setattr(adapter, "_run_git_apply", fake_apply)

    with pytest.raises(RepositoryOperationError, match="simulated failure"):
        adapter.apply_patch(patch)

    assert (repository / "app.py").read_text() == "old\n"
