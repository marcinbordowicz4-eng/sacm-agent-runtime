import subprocess


class DockerExecutor:
    def run(self, image: str, command: list[str]) -> dict:
        result = subprocess.run(
            ["docker", "run", "--rm", image, *command],
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
