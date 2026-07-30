import pytest

from sacm.adapters.repository_adapter import RepositoryAdapter


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
