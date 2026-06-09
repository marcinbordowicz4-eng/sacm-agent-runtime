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


def test_read_file(temp_repo):
    adapter = RepositoryAdapter(str(temp_repo))
    content = adapter.read_file("README.md")
    assert "# Test Repo" in content


def test_write_file(temp_repo):
    adapter = RepositoryAdapter(str(temp_repo))
    adapter.write_file("new_file.txt", "new content")
    assert (temp_repo / "new_file.txt").read_text() == "new content"
