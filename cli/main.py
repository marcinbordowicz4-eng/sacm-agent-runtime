import httpx
import typer
from rich.console import Console

app = typer.Typer(help="SACM Agent Runtime CLI")
console = Console()

BASE_URL = "http://localhost:8000"


@app.command()
def init() -> None:
    """Initialize SACM configuration."""
    console.print("[green]SACM initialized[/green]")


@app.command("register-repo")
def register_repo(repo_path: str) -> None:
    """Register a target repository."""
    console.print(f"[blue]Registering repository: {repo_path}[/blue]")
    response = httpx.post(f"{BASE_URL}/repository/analyze", json={"repo_path": repo_path})
    response.raise_for_status()
    console.print(response.json())


@app.command()
def run(task_description: str, repo: str | None = typer.Option(None, "--repo")) -> None:
    """Create and run a task."""
    payload = {"title": task_description[:80], "description": task_description}
    if repo:
        payload["target_repo_path"] = repo
    response = httpx.post(f"{BASE_URL}/tasks", json=payload)
    response.raise_for_status()
    task = response.json()
    task_id = task["id"]
    console.print(f"[blue]Task created: {task_id}[/blue]")
    run_response = httpx.post(f"{BASE_URL}/tasks/{task_id}/run", timeout=120)
    run_response.raise_for_status()
    console.print(run_response.json())


@app.command()
def events(task_id: str) -> None:
    """List task events."""
    response = httpx.get(f"{BASE_URL}/tasks/{task_id}/events")
    response.raise_for_status()
    for event in response.json():
        console.print(event)


@app.command()
def memory(task_id: str) -> None:
    """List task memory."""
    response = httpx.get(f"{BASE_URL}/tasks/{task_id}/memory")
    response.raise_for_status()
    for chunk in response.json():
        console.print(chunk)


@app.command()
def diff(task_id: str) -> None:
    """Show task artifacts."""
    response = httpx.get(f"{BASE_URL}/tasks/{task_id}/artifacts")
    response.raise_for_status()
    console.print(response.json())


if __name__ == "__main__":
    app()
