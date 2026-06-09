from sacm.adapters.repository_adapter import RepositoryAdapter


class WorktreeManager:
    def create(self, repo_path: str, branch_name: str) -> str:
        return RepositoryAdapter(repo_path).create_worktree(branch_name)
