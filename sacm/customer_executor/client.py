from __future__ import annotations

import hashlib
import ssl
from typing import Any, Protocol

import httpx

from sacm.customer_executor.config import ExecutorSettings


class ControlPlaneError(RuntimeError):
    pass


class ExecutorRevoked(ControlPlaneError):
    pass


class ControlPlaneClient(Protocol):
    def enroll(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def lease(self, lease_seconds: int) -> dict[str, Any] | None: ...

    def start(self, job_id: str, lease_token: str) -> dict[str, Any]: ...

    def heartbeat_job(
        self, job_id: str, lease_token: str, lease_seconds: int
    ) -> dict[str, Any]: ...

    def complete(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    def fail(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    def rotate(
        self, public_signing_key: str, signing_key_fingerprint: str
    ) -> dict[str, Any]: ...

    def close(self) -> None: ...


class HttpxControlPlaneClient:
    def __init__(self, settings: ExecutorSettings, token: str | None = None) -> None:
        self.settings = settings
        self.token = token
        tls = settings.network_boundary.tls
        verify: bool | str | ssl.SSLContext = (
            str(tls.ca_bundle) if tls.ca_bundle else True
        )
        cert: tuple[str, str] | None = None
        if tls.client_certificate and tls.client_key:
            cert = (str(tls.client_certificate), str(tls.client_key))
        self._expected_fingerprint = (
            tls.server_certificate_sha256.lower()
            if tls.server_certificate_sha256
            else None
        )
        self._client = httpx.Client(
            base_url=settings.control_plane_url.rstrip("/"),
            timeout=settings.request_timeout_seconds,
            verify=verify,
            cert=cert,
            proxy=settings.network_boundary.proxy_url,
            trust_env=False,
            event_hooks={"response": [self._verify_server_fingerprint]},
            headers={"User-Agent": f"sacm-customer-executor/{settings.version}"},
        )

    def enroll(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._required(
            self._request("POST", "/v1/executors/enroll", json=payload)
        )

    def heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._required(
            self._request("POST", "/v1/executor/heartbeat", json=payload)
        )

    def lease(self, lease_seconds: int) -> dict[str, Any] | None:
        return self._request(
            "POST",
            "/v1/executor/jobs/lease",
            json={"lease_seconds": lease_seconds},
            allow_empty=True,
        )

    def start(self, job_id: str, lease_token: str) -> dict[str, Any]:
        return self._required(
            self._request(
                "POST",
                f"/v1/executor/jobs/{job_id}/start",
                json={"lease_token": lease_token},
            )
        )

    def heartbeat_job(
        self, job_id: str, lease_token: str, lease_seconds: int
    ) -> dict[str, Any]:
        return self._required(
            self._request(
                "POST",
                f"/v1/executor/jobs/{job_id}/heartbeat",
                json={"lease_token": lease_token, "lease_seconds": lease_seconds},
            )
        )

    def complete(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._required(
            self._request(
                "POST", f"/v1/executor/jobs/{job_id}/complete", json=payload
            )
        )

    def fail(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._required(
            self._request("POST", f"/v1/executor/jobs/{job_id}/fail", json=payload)
        )

    def rotate(
        self, public_signing_key: str, signing_key_fingerprint: str
    ) -> dict[str, Any]:
        result = self._required(
            self._request(
                "POST",
                "/v1/executor/rotate",
                json={
                    "public_signing_key": public_signing_key,
                    "signing_key_fingerprint": signing_key_fingerprint,
                },
            )
        )
        self.token = result["auth_token"]
        return result

    def close(self) -> None:
        self._client.close()

    def _request(
        self, method: str, path: str, *, allow_empty: bool = False, **kwargs: Any
    ) -> dict[str, Any] | None:
        headers = kwargs.pop("headers", {})
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = self._client.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise ControlPlaneError(f"Control-plane request failed: {type(exc).__name__}") from exc
        if response.status_code == 401:
            raise ExecutorRevoked("Executor credentials were rejected or revoked.")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ControlPlaneError(
                f"Control-plane request failed with HTTP {response.status_code}."
            ) from exc
        if allow_empty and response.status_code == 204:
            return None
        value = response.json()
        if not isinstance(value, dict):
            raise ControlPlaneError("Control-plane response was not a JSON object.")
        return value

    @staticmethod
    def _required(value: dict[str, Any] | None) -> dict[str, Any]:
        if value is None:
            raise ControlPlaneError("Control-plane response body was unexpectedly empty.")
        return value

    def _verify_server_fingerprint(self, response: httpx.Response) -> None:
        if not self._expected_fingerprint:
            return
        stream = response.extensions.get("network_stream")
        ssl_object = stream.get_extra_info("ssl_object") if stream else None
        if ssl_object is None:
            raise ControlPlaneError("TLS certificate pinning could not inspect the peer.")
        actual = hashlib.sha256(ssl_object.getpeercert(binary_form=True)).hexdigest()
        if actual.lower() != self._expected_fingerprint:
            raise ControlPlaneError("Control-plane TLS certificate fingerprint mismatch.")
