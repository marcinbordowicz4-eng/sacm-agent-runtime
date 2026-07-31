from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import typer
from rich.console import Console

from sacm.customer_executor.client import HttpxControlPlaneClient
from sacm.customer_executor.config import ExecutorSettings
from sacm.customer_executor.daemon import CustomerExecutorDaemon
from sacm.customer_executor.identity import IdentityStore

app = typer.Typer(help="Operate the SACM customer-hosted executor.")
console = Console()


def _settings(config: Path) -> ExecutorSettings:
    return ExecutorSettings.load(config)


@app.command("validate-config")
def validate_config(
    config: Path = typer.Option(..., "--config", exists=True, dir_okay=False)
) -> None:
    """Validate executor configuration and network security policy."""
    settings = _settings(config)
    console.print_json(data=settings.public_status())


@app.command()
def enroll(
    enrollment_token: str = typer.Option(..., "--enrollment-token", prompt=True, hide_input=True),
    config: Path = typer.Option(..., "--config", exists=True, dir_okay=False),
) -> None:
    """Generate an identity and enroll it using a single-use bootstrap token."""
    settings = _settings(config)
    identity = IdentityStore(settings.state_dir)
    identity.initialize()
    client = HttpxControlPlaneClient(settings)
    try:
        response = client.enroll(
            {
                "enrollment_token": enrollment_token,
                "executor_identity": settings.executor_identity,
                "display_name": settings.display_name,
                "capabilities": settings.capabilities,
                "labels": settings.labels,
                "runtime_kind": settings.runtime_kind,
                "sandbox_runtime": settings.sandbox_runtime,
                "sandbox_policy": {
                    "schema_version": "sandbox-policy/v1",
                    "runtime": settings.sandbox_runtime,
                    "host_runtime_verified": settings.sandbox_verified,
                    "verification_command": "operator-attested deployment preflight",
                    "isolation": "user-space-kernel",
                    "network_mode": "restricted-egress",
                    "no_new_privileges": True,
                },
                "public_signing_key": identity.public_key_pem(),
                "version": settings.version,
                "network_boundary": settings.network_boundary.public_metadata(),
                "storage_region": settings.network_boundary.residency_region,
                "storage_classification": settings.storage_classification,
                "storage_class": settings.storage_class,
            }
        )
        identity.write_token(response["auth_token"])
        identity.write_metadata(response["executor"])
    finally:
        client.close()
    console.print("[green]Executor enrolled; credentials stored with mode 0600.[/green]")


@app.command()
def run(
    config: Path = typer.Option(..., "--config", exists=True, dir_okay=False),
    once: bool = typer.Option(False, "--once", help="Process at most one lease poll."),
) -> None:
    """Run the lease, heartbeat, isolated execution, and result submission loop."""
    settings = _settings(config)
    identity = IdentityStore(settings.state_dir)
    identity.initialize()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    daemon = CustomerExecutorDaemon(
        settings,
        identity,
        HttpxControlPlaneClient(settings, identity.token()),
    )
    daemon.install_signal_handlers()
    daemon.run(once=once)


@app.command()
def status(
    config: Path = typer.Option(..., "--config", exists=True, dir_okay=False),
    local_health: bool = typer.Option(True, "--local-health/--state-only"),
) -> None:
    """Show non-sensitive local identity, drain, and daemon health state."""
    settings = _settings(config)
    identity = IdentityStore(settings.state_dir)
    value = {
        "enrolled": identity.token_path.exists(),
        "draining": identity.draining,
        "metadata": identity.metadata(),
    }
    if local_health:
        try:
            value["daemon"] = httpx.get(
                f"http://{settings.health_bind}:{settings.health_port}/status",
                timeout=2,
            ).json()
        except httpx.HTTPError:
            value["daemon"] = {"status": "unreachable"}
    console.print_json(data=value)


@app.command()
def rotate(
    config: Path = typer.Option(..., "--config", exists=True, dir_okay=False)
) -> None:
    """Atomically rotate the Ed25519 identity key and executor bearer token."""
    settings = _settings(config)
    identity = IdentityStore(settings.state_dir)
    identity.initialize()
    identity.set_drain(True, "identity rotation")
    client = HttpxControlPlaneClient(settings, identity.token())
    try:
        response = identity.rotate_with(client.rotate)
        identity.write_token(response["auth_token"])
        identity.write_metadata(response["executor"])
        identity.set_drain(False)
    finally:
        client.close()
    console.print("[green]Executor identity and auth token rotated.[/green]")


@app.command("revoke-preparation")
def revoke_preparation(
    config: Path = typer.Option(..., "--config", exists=True, dir_okay=False),
    reason: str = typer.Option("operator revocation preparation", "--reason"),
) -> None:
    """Enter drain mode and create a non-secret revocation handoff record."""
    settings = _settings(config)
    identity = IdentityStore(settings.state_dir)
    identity.initialize()
    identity.set_drain(True, reason)
    metadata = identity.metadata()
    handoff = {
        "executor_id": metadata.get("id"),
        "executor_identity": settings.executor_identity,
        "signing_key_fingerprint": identity.fingerprint(),
        "reason": reason,
        "prepared": True,
    }
    path = settings.state_dir / "revocation-request.json"
    path.write_text(json.dumps(handoff, sort_keys=True, indent=2), encoding="utf-8")
    path.chmod(0o600)
    console.print_json(data=handoff)


@app.command()
def drain(
    config: Path = typer.Option(..., "--config", exists=True, dir_okay=False),
    reason: str = typer.Option("operator drain", "--reason"),
) -> None:
    """Stop acquiring new leases while allowing the active lease to finish."""
    settings = _settings(config)
    identity = IdentityStore(settings.state_dir)
    identity.initialize()
    identity.set_drain(True, reason)
    console.print("[yellow]Executor is draining.[/yellow]")


@app.command()
def resume(
    config: Path = typer.Option(..., "--config", exists=True, dir_okay=False)
) -> None:
    """Clear an operator drain after compatibility and revocation checks."""
    settings = _settings(config)
    identity = IdentityStore(settings.state_dir)
    identity.initialize()
    identity.set_drain(False)
    console.print("[green]Executor drain cleared.[/green]")


if __name__ == "__main__":
    app()
