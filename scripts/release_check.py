#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import yaml
from alembic.config import Config
from alembic.script import ScriptDirectory

from sacm.core.benchmark_service import load_suite, validate_suite
from sacm.security_release_gate import verify_signed_report

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_MIGRATION_HEAD = "c8d1e4f7a2b5"


@dataclass
class Check:
    name: str
    status: str
    detail: str


def _run(command: Sequence[str], *, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(
            command,
            127,
            stdout=f"command not found: {command[0]}",
        )


def _record(checks: list[Check], name: str, ok: bool, detail: str) -> None:
    checks.append(Check(name, "PASS" if ok else "FAIL", detail))


def migration_heads() -> list[str]:
    config = Config()
    config.set_main_option("script_location", str(ROOT / "sacm" / "migrations"))
    return list(ScriptDirectory.from_config(config).get_heads())


def workflow_pin_errors() -> list[str]:
    errors: list[str] = []
    pattern = re.compile(r"uses:\s*([^@\s]+)@([^\s#]+)")
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        content = path.read_text(encoding="utf-8")
        try:
            yaml.safe_load(content)
        except yaml.YAMLError as exc:
            errors.append(f"{path.name}: invalid YAML: {exc}")
            continue
        for line_number, line in enumerate(content.splitlines(), start=1):
            match = pattern.search(line)
            if not match or match.group(1).startswith("./"):
                continue
            if not re.fullmatch(r"[0-9a-f]{40}", match.group(2)):
                errors.append(f"{path.name}:{line_number}: {match.group(0)}")
    return errors


def security_gate_check(path: Path, git_sha: str) -> Check:
    signed_path = path / "release-security-report.signed.json"
    if not signed_path.is_file():
        return Check(
            "security gate",
            "INCOMPLETE",
            f"missing required signed report: {signed_path}",
        )
    try:
        signed = json.loads(signed_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return Check("security gate", "INCOMPLETE", f"invalid signed report: {exc}")
    valid, errors = verify_signed_report(signed)
    report = signed.get("statement", {}).get("predicate", {})
    if not valid:
        return Check("security gate", "FAIL", "; ".join(errors))
    if report.get("git_sha") != git_sha:
        return Check(
            "security gate",
            "FAIL",
            "signed report commit does not match the release commit",
        )
    status = report.get("status")
    if status != "PASS":
        return Check(
            "security gate",
            "INCOMPLETE" if status == "INCOMPLETE" else "FAIL",
            f"required security gate status is PASS, found {status!r}",
        )
    return Check("security gate", "PASS", f"signed PASS report for {git_sha}")


def verify_wheel(version: str) -> tuple[bool, str]:
    wheels = sorted((ROOT / "dist").glob("*.whl"))
    if len(wheels) != 1:
        return False, f"expected one wheel, found {len(wheels)}"
    wheel = wheels[0]
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            return False, "wheel has no unique METADATA file"
        metadata = archive.read(metadata_names[0]).decode()
        required = {
            "apps/api/main.py",
            "cli/main.py",
            "sacm/customer_executor/cli.py",
            "sacm/migrations/versions/c8d1e4f7a2b5_jira_e2e_delivery.py",
        }
        missing = sorted(required - set(names))
        if f"Version: {version}\n" not in metadata:
            return False, f"wheel metadata is not version {version}"
        if missing:
            return False, "wheel is missing: " + ", ".join(missing)
        if any(name.startswith(".sacm/") for name in names):
            return False, "wheel unexpectedly contains .sacm content"
    return True, wheel.name


def verify_sdist(version: str) -> tuple[bool, str]:
    sdists = sorted((ROOT / "dist").glob("*.tar.gz"))
    if len(sdists) != 1:
        return False, f"expected one sdist, found {len(sdists)}"
    sdist = sdists[0]
    prefix = f"sacm_agent_runtime-{version}/"
    with tarfile.open(sdist, "r:gz") as archive:
        names = archive.getnames()
    required = {
        prefix + "pyproject.toml",
        prefix + "apps/api/main.py",
        prefix + "sacm/customer_executor/cli.py",
    }
    missing = sorted(required - set(names))
    forbidden = [
        name
        for name in names
        if "/.sacm/" in f"/{name}/" or "/.playwright-mcp/" in f"/{name}/"
    ]
    if missing:
        return False, "sdist is missing: " + ", ".join(missing)
    if forbidden:
        return False, "sdist contains private/generated content: " + ", ".join(forbidden)
    return True, sdist.name


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a SACM release candidate.")
    parser.add_argument("--version", required=True)
    parser.add_argument("--tag")
    parser.add_argument(
        "--security-artifacts",
        default="security-release-evidence",
        type=Path,
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Permit a dirty tree only while preparing a local dry-run.",
    )
    args = parser.parse_args()
    if args.allow_dirty and not args.dry_run:
        parser.error("--allow-dirty is valid only with --dry-run")

    version = args.version
    tag = args.tag or f"v{version}"
    checks: list[Check] = []
    head = _run(["git", "rev-parse", "HEAD"]).stdout.strip()
    dirty = _run(["git", "status", "--porcelain", "--untracked-files=all"]).stdout
    _record(
        checks,
        "clean tree",
        not dirty or (args.dry_run and args.allow_dirty),
        "clean"
        if not dirty
        else "dirty tree explicitly permitted for preparation dry-run"
        if args.dry_run and args.allow_dirty
        else "working tree has uncommitted changes",
    )

    exact_tags = _run(["git", "tag", "--points-at", "HEAD"]).stdout.splitlines()
    tag_ok = tag in exact_tags or args.dry_run
    _record(
        checks,
        "release tag",
        tag_ok,
        f"{tag} points at HEAD"
        if tag in exact_tags
        else f"{tag} is not present yet; accepted only for dry-run"
        if args.dry_run
        else f"{tag} does not point at HEAD",
    )

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    surfaces = {
        "package": project["project"]["version"] == version,
        "runtime": f'__version__ = "{version}"'
        in (ROOT / "sacm" / "__init__.py").read_text(encoding="utf-8"),
        "API": "version=__version__"
        in (ROOT / "apps" / "api" / "main.py").read_text(encoding="utf-8"),
        "Docker": f"ARG VERSION={version}"
        in (ROOT / "Dockerfile").read_text(encoding="utf-8"),
        "customer executor": f"version: {version}"
        in (ROOT / "deploy" / "customer-executor" / "config.example.yaml").read_text(
            encoding="utf-8"
        ),
        "customer executor Kubernetes": f"version: {version}"
        in (ROOT / "deploy" / "kubernetes" / "customer-executor.yaml").read_text(
            encoding="utf-8"
        ),
        "customer executor runtime": "default=__version__"
        in (ROOT / "sacm" / "customer_executor" / "config.py").read_text(
            encoding="utf-8"
        ),
        "production executor policy": (
            f"SACM_EXECUTOR_CURRENT_VERSION={version}"
            in (ROOT / "production.env.example").read_text(encoding="utf-8")
            and f"SACM_EXECUTOR_MINIMUM_VERSION={version}"
            in (ROOT / "production.env.example").read_text(encoding="utf-8")
        ),
    }
    _record(
        checks,
        "version surfaces",
        all(surfaces.values()),
        ", ".join(f"{name}={'ok' if ok else 'mismatch'}" for name, ok in surfaces.items()),
    )
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    _record(
        checks,
        "changelog",
        f"## {version} - " in changelog,
        f"{version} release entry {'found' if f'## {version} - ' in changelog else 'missing'}",
    )
    heads = migration_heads()
    _record(
        checks,
        "migration head",
        heads == [EXPECTED_MIGRATION_HEAD],
        f"heads={heads}",
    )
    pin_errors = workflow_pin_errors()
    _record(
        checks,
        "workflow action pins",
        not pin_errors,
        "all external actions use immutable SHA pins"
        if not pin_errors
        else "; ".join(pin_errors),
    )

    try:
        benchmark = validate_suite(load_suite(ROOT / "benchmarks" / "suite-v2.json"))
        _record(
            checks,
            "Benchmark 100",
            benchmark["case_count"] == 100,
            "suite valid; status=NOT_RUN; no performance or product-quality claim",
        )
    except Exception as exc:
        _record(checks, "Benchmark 100", False, str(exc))

    docker = _run(["docker", "version", "--format", "{{.Server.Version}}"])
    if docker.returncode == 0:
        checks.append(Check("Docker", "PASS", docker.stdout.strip()))
    else:
        checks.append(
            Check(
                "Docker",
                "INCOMPLETE" if args.dry_run else "FAIL",
                docker.stdout.strip() or "Docker daemon unavailable",
            )
        )

    commands = [
        ("pytest", [sys.executable, "-m", "pytest", "-q"]),
        ("Ruff", [sys.executable, "-m", "ruff", "check", "sacm", "apps", "cli", "tests", "scripts"]),
        ("mypy", [sys.executable, "-m", "mypy", "sacm", "apps", "cli"]),
        ("dashboard install", ["npm", "ci"]),
        ("dashboard lint", ["npm", "run", "lint"]),
        ("dashboard build", ["npm", "run", "build"]),
    ]
    for name, command in commands:
        cwd = ROOT / "apps" / "dashboard" if name.startswith("dashboard") else ROOT
        result = _run(command, cwd=cwd)
        _record(
            checks,
            name,
            result.returncode == 0,
            "passed" if result.returncode == 0 else result.stdout[-2000:].strip(),
        )

    shutil.rmtree(ROOT / "dist", ignore_errors=True)
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    build = _run([sys.executable, "-m", "build"])
    _record(
        checks,
        "Python build",
        build.returncode == 0,
        "sdist and wheel built" if build.returncode == 0 else build.stdout[-2000:].strip(),
    )
    if build.returncode == 0:
        wheel_ok, wheel_detail = verify_wheel(version)
        _record(checks, "wheel contents/version", wheel_ok, wheel_detail)
        sdist_ok, sdist_detail = verify_sdist(version)
        _record(checks, "sdist contents/version", sdist_ok, sdist_detail)

    checks.append(security_gate_check(ROOT / args.security_artifacts, head))
    for check in checks:
        print(f"[{check.status}] {check.name}: {check.detail}")

    if any(check.status == "FAIL" for check in checks):
        return 1
    if any(check.status == "INCOMPLETE" for check in checks):
        return 2
    print(f"Release {tag} is ready. Benchmark 100 remains NOT_RUN.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
