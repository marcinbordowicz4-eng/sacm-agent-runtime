import subprocess
from pathlib import Path


class ShellAdapter:
    def run(self, command: str, cwd: str | None = None) -> dict:
        result = subprocess.run(
            command,
            shell=True,
            cwd=Path(cwd).resolve() if cwd else None,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
