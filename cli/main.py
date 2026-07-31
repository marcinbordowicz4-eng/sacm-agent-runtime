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


@app.command("jira-e2e-demo")
def jira_e2e_demo() -> None:
    """Run the deterministic Jira-to-delivery scenario fully offline."""
    from sacm.demo.jira_e2e import run_demo

    console.print("[yellow]External Jira, executor, and GitHub services are simulated.[/yellow]")
    console.print(run_demo())


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


@benchmark_app.command("generate")
def generate_benchmark_fixtures(
    destination: str,
    suite: str = "benchmarks/suite-v2.json",
    case_id: list[str] | None = typer.Option(None, "--case-id"),
) -> None:
    """Generate deterministic, pinned local fixture repositories."""
    from sacm.core.benchmark_service import FixtureGenerator, load_suite

    manifest = FixtureGenerator(load_suite(suite)).generate(
        destination, set(case_id or []) or None
    )
    console.print(
        {
            "status": "GENERATED",
            "case_count": len(manifest["generated"]),
            "destination": destination,
        }
    )


@benchmark_app.command("validate")
def validate_benchmark(
    suite: str = "benchmarks/suite-v2.json",
    report: str | None = typer.Option(None, "--report"),
) -> None:
    """Validate the v2 suite and, optionally, a complete evidence report."""
    import json
    from pathlib import Path

    from sacm.core.benchmark_service import (
        BenchmarkReportV2,
        load_suite,
        validate_report,
        validate_suite,
    )

    loaded = load_suite(suite)
    result = validate_suite(loaded)
    if report:
        evidence_report = BenchmarkReportV2.model_validate(
            json.loads(Path(report).read_text(encoding="utf-8"))
        )
        result["report"] = validate_report(evidence_report, loaded)
    console.print(result)


@benchmark_app.command("run")
def run_benchmark(
    runner: str = typer.Option(..., "--runner", help="baseline-command or sacm-execution-plane"),
    suite: str = "benchmarks/suite-v2.json",
    fixtures: str = "benchmark-work",
    config: str | None = typer.Option(None, "--config"),
    output: str = "benchmark-report-v2.json",
    ablation: str | None = typer.Option(None, "--ablation"),
) -> None:
    """Run real external agents, or truthfully emit NOT_RUN when unconfigured."""
    import json
    from pathlib import Path

    from sacm.core.benchmark_service import (
        BenchmarkService,
        ExecutionConfigV2,
        load_suite,
    )

    if runner not in {"baseline-command", "sacm-execution-plane"}:
        raise typer.BadParameter("runner must be baseline-command or sacm-execution-plane")
    config_data = (
        json.loads(Path(config).read_text(encoding="utf-8")) if config else {}
    )
    config_data["runner"] = runner
    config_data.setdefault(
        "ablation",
        ablation
        or (
            "single-agent-baseline"
            if runner == "baseline-command"
            else "sacm-full"
        ),
    )
    execution_config = ExecutionConfigV2.model_validate(config_data)
    report = BenchmarkService.run(load_suite(suite), execution_config, fixtures)
    Path(output).write_text(
        report.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    console.print(
        f"[bold yellow]STATUS: {report.status} — {report.truthful_status}[/bold yellow]"
    )


@benchmark_app.command("compare")
def compare_benchmarks(
    baseline: str,
    candidate: str,
    suite: str = "benchmarks/suite-v2.json",
    output: str = "benchmark-comparison-v2.json",
    minimum_paired_sample: int = 10,
) -> None:
    """Compare valid paired reports with deterministic bootstrap intervals."""
    import json
    from pathlib import Path

    from sacm.core.benchmark_service import (
        BenchmarkReportV2,
        compare_reports,
        load_suite,
    )

    comparison = compare_reports(
        BenchmarkReportV2.model_validate_json(
            Path(baseline).read_text(encoding="utf-8")
        ),
        BenchmarkReportV2.model_validate_json(
            Path(candidate).read_text(encoding="utf-8")
        ),
        load_suite(suite),
        minimum_paired_sample=minimum_paired_sample,
    )
    Path(output).write_text(
        json.dumps(comparison, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    console.print(
        f"[bold yellow]STATUS: {comparison['status']} — "
        f"{comparison['truthful_status']}[/bold yellow]"
    )


@benchmark_app.command("report")
def render_benchmark_report(source: str, output: str = "benchmark-report-v2.md") -> None:
    """Render report or comparison JSON as Markdown with prominent status."""
    import json
    from pathlib import Path

    from sacm.core.benchmark_service import render_markdown

    payload = json.loads(Path(source).read_text(encoding="utf-8"))
    Path(output).write_text(render_markdown(payload), encoding="utf-8")
    console.print(f"[green]Wrote {output}[/green]")


if __name__ == "__main__":
    app()
