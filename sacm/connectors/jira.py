import base64
import hashlib
import hmac
import json
import os
import time
from collections.abc import Callable
from typing import Any, Protocol
from urllib.parse import quote

import httpx


class JiraError(RuntimeError):
    pass


class JiraTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any] | None,
        timeout: float,
    ) -> httpx.Response: ...


class HttpxJiraTransport:
    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self.client = httpx.Client(transport=transport)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any] | None,
        timeout: float,
    ) -> httpx.Response:
        return self.client.request(
            method, url, headers=headers, json=json, timeout=timeout
        )


SecretResolver = Callable[[str], str]


def environment_secret_resolver(reference: str) -> str:
    name = reference.removeprefix("env:")
    if not name or name == reference:
        raise JiraError("Only env: secret references are supported by this runtime.")
    value = os.getenv(name)
    if not value:
        raise JiraError("Configured Jira secret reference is unavailable.")
    return value


class JiraCloudClient:
    """Small Jira Cloud REST v3 client with injectable transport and bounded retries."""

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        api_token_ref: str,
        secret_resolver: SecretResolver = environment_secret_resolver,
        transport: JiraTransport | None = None,
        timeout_seconds: float = 10,
        max_attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.api_token_ref = api_token_ref
        self.secret_resolver = secret_resolver
        self.transport = transport or HttpxJiraTransport()
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.sleep = sleep

    def issue(self, issue_key: str) -> dict[str, Any]:
        return self._request("GET", f"/rest/api/3/issue/{quote(issue_key)}")

    def comments(self, issue_key: str) -> list[dict[str, Any]]:
        payload = self._request(
            "GET", f"/rest/api/3/issue/{quote(issue_key)}/comment?maxResults=100"
        )
        values = payload.get("comments", [])
        return values if isinstance(values, list) else []

    def create_comment(self, issue_key: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/rest/api/3/issue/{quote(issue_key)}/comment",
            {"body": body},
            retry_write=False,
        )

    def update_comment(
        self, issue_key: str, comment_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return self._request(
            "PUT",
            f"/rest/api/3/issue/{quote(issue_key)}/comment/{quote(comment_id)}",
            {"body": body},
        )

    def transitions(self, issue_key: str) -> list[dict[str, Any]]:
        payload = self._request(
            "GET", f"/rest/api/3/issue/{quote(issue_key)}/transitions"
        )
        values = payload.get("transitions", [])
        return values if isinstance(values, list) else []

    def transition(self, issue_key: str, transition_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/rest/api/3/issue/{quote(issue_key)}/transitions",
            {"transition": {"id": transition_id}},
            retry_write=False,
        )

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        retry_write: bool = True,
    ) -> dict[str, Any]:
        token = self.secret_resolver(self.api_token_ref)
        basic = base64.b64encode(f"{self.username}:{token}".encode()).decode()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Basic {basic}",
        }
        last_error = "Jira request failed."
        attempts = self.max_attempts if retry_write or method in {"GET", "PUT"} else 1
        for attempt in range(1, attempts + 1):
            try:
                response = self.transport.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=headers,
                    json=payload,
                    timeout=self.timeout_seconds,
                )
                if response.status_code < 400:
                    if not response.content:
                        return {}
                    value = response.json()
                    return value if isinstance(value, dict) else {"value": value}
                last_error = f"Jira returned HTTP {response.status_code}."
                retryable = response.status_code in {408, 409, 425, 429} or (
                    response.status_code >= 500
                )
                if not retryable:
                    raise JiraError(last_error)
            except JiraError:
                raise
            except (httpx.TimeoutException, httpx.TransportError, ValueError):
                last_error = "Jira request failed due to a retryable transport error."
            if attempt < attempts:
                self.sleep(min(0.25 * (2 ** (attempt - 1)), 2.0))
        raise JiraError(last_error)


def verify_webhook_signature(
    body: bytes, signature: str | None, secret: str | None
) -> bool:
    if not secret:
        return True
    if not signature:
        return False
    supplied = signature.removeprefix("sha256=")
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, supplied)


def adf_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(filter(None, (adf_text(item) for item in value)))
    if isinstance(value, dict):
        if value.get("type") == "text" and isinstance(value.get("text"), str):
            return value["text"].strip()
        return adf_text(value.get("content", []))
    return ""


def adf_document(text: str) -> dict[str, Any]:
    paragraphs = [
        {
            "type": "paragraph",
            "content": [{"type": "text", "text": line or " "}],
        }
        for line in text.splitlines()
    ]
    return {"type": "doc", "version": 1, "content": paragraphs or []}


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
