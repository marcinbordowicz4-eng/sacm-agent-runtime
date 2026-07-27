import httpx
import typer
from rich.console import Console

app = typer.Typer(help="SACM Agent Runtime CLI")
runs_app = typer.Typer(help="Manage durable SACM runs.")
benchmark_app = typer.Typer(help="Compare SACM benchmark reports.")
app.add_typer(runs_app, name="runs")
app.add_typer(benchmark_app, name="benchmark")
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


@runs_app.command("create")
def create_run(
    task_description: str,
    repo: str | None = typer.Option(None, "--repo"),
) -> None:
    """Create a persistent v1 run without executing it."""
    payload = {"title": task_description[:80], "description": task_description}
    if repo:
        payload["target_repo_path"] = repo
    response = httpx.post(f"{BASE_URL}/v1/runs", json=payload)
    response.raise_for_status()
    console.print(response.json())


@runs_app.command("inspect")
def inspect_run(run_id: str) -> None:
    """Show durable run state, steps, and events."""
    response = httpx.get(f"{BASE_URL}/v1/runs/{run_id}")
    response.raise_for_status()
    console.print(response.json())


@runs_app.command("execute")
def execute_run(run_id: str) -> None:
    """Execute a persistent local run."""
    response = httpx.post(f"{BASE_URL}/v1/runs/{run_id}/execute", timeout=600)
    response.raise_for_status()
    console.print(response.json())


@runs_app.command("cancel")
def cancel_run(run_id: str) -> None:
    """Cancel a run before further workflow work is scheduled."""
    response = httpx.post(f"{BASE_URL}/v1/runs/{run_id}/cancel")
    response.raise_for_status()
    console.print(response.json())


@runs_app.command("resume")
def resume_run(run_id: str) -> None:
    """Resume a failed run from its persisted state."""
    response = httpx.post(f"{BASE_URL}/v1/runs/{run_id}/resume")
    response.raise_for_status()
    console.print(response.json())


@runs_app.command("retry")
def retry_run_step(run_id: str, step_id: str) -> None:
    """Schedule one failed run step for retry."""
    response = httpx.post(f"{BASE_URL}/v1/runs/{run_id}/steps/{step_id}/retry")
    response.raise_for_status()
    console.print(response.json())


@runs_app.command("evidence")
def run_evidence(run_id: str) -> None:
    """Build the run's minimal hash-checked evidence pack."""
    response = httpx.post(f"{BASE_URL}/v1/runs/{run_id}/evidence")
    response.raise_for_status()
    console.print(response.json())


@benchmark_app.command("compare")
def compare_benchmarks(baseline: str, candidate: str) -> None:
    """Compare two JSON reports produced by BenchmarkService."""
    import json
    from pathlib import Path

    from sacm.core.benchmark_service import BenchmarkService

    console.print(
        BenchmarkService.compare(
            json.loads(Path(baseline).read_text(encoding="utf-8")),
            json.loads(Path(candidate).read_text(encoding="utf-8")),
        )
    )


@benchmark_app.command("run")
def run_benchmark(suite: str, output: str = "benchmark-report.json") -> None:
    """Execute an explicit suite through the durable-runs API."""
    import json
    from pathlib import Path

    from sacm.core.benchmark_service import BenchmarkService

    service = BenchmarkService()

    def execute(case):
        payload = {"title": case.title, "description": case.description}
        if case.target_repo_path:
            payload["target_repo_path"] = case.target_repo_path
        created = httpx.post(f"{BASE_URL}/v1/runs", json=payload)
        created.raise_for_status()
        run_id = created.json()["id"]
        completed = httpx.post(
            f"{BASE_URL}/v1/runs/{run_id}/execute", timeout=600
        )
        completed.raise_for_status()
        return completed.json()

    report = service.run(service.load_suite(suite), execute)
    Path(output).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    console.print(report)


@benchmark_app.command("validate")
def validate_benchmark_suite(suite: str, minimum_cases: int = 50) -> None:
    """Validate that a suite is ready for a full benchmark run."""
    from sacm.core.benchmark_service import BenchmarkService

    service = BenchmarkService()
    console.print(
        service.validate_suite(
            service.load_suite(suite), minimum_cases=minimum_cases
        )
    )


if __name__ == "__main__":
    app()
