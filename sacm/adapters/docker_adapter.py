import subprocess


class DockerAdapter:
    def compose_up(self) -> dict:
        result = subprocess.run(
            ["docker-compose", "config"],
            capture_output=True,
            text=True,
            check=False,
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
