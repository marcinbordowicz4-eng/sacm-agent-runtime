import os
import time
from pathlib import Path

import httpx


class GitHubAppAdapter:
    """GitHub App installation-token client; secrets are read only at call time."""

    def __init__(self) -> None:
        app_id = os.getenv("SACM_GITHUB_APP_ID")
        key_path = os.getenv("SACM_GITHUB_APP_PRIVATE_KEY_PATH")
        if not app_id or not key_path:
            raise RuntimeError(
                "SACM_GITHUB_APP_ID and SACM_GITHUB_APP_PRIVATE_KEY_PATH are required."
            )
        self.app_id = app_id
        self.private_key = Path(key_path).read_text(encoding="utf-8")
        self.api_url = os.getenv("SACM_GITHUB_API_URL", "https://api.github.com")

    def installation_token(self, installation_id: int) -> str:
        response = httpx.post(
            f"{self.api_url}/app/installations/{installation_id}/access_tokens",
            headers=self._headers(),
            timeout=10,
        )
        response.raise_for_status()
        token = response.json().get("token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("GitHub App did not return an installation token.")
        return token

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._app_jwt()}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _app_jwt(self) -> str:
        try:
            import jwt
        except ImportError as exc:
            raise RuntimeError(
                "GitHub App support requires: pip install -e '.[github-app]'"
            ) from exc
        now = int(time.time())
        return jwt.encode(
            {"iat": now - 60, "exp": now + 540, "iss": self.app_id},
            self.private_key,
            algorithm="RS256",
        )
