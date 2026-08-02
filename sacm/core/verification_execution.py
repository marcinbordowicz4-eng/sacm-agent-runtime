import re
import shlex
from typing import Any

_RESOURCE_PATTERNS = (
    re.compile(r"\bexit(?:ed)?(?: with)?(?: code)? 137\b", re.IGNORECASE),
    re.compile(r"\bsigkill\b", re.IGNORECASE),
    re.compile(r"\bheap out of memory\b", re.IGNORECASE),
    re.compile(r"\bout of memory\b", re.IGNORECASE),
    re.compile(r"\boom(?:[-_ ]?kill(?:ed|er)?)?\b", re.IGNORECASE),
    re.compile(r"\bworker\b.*\b(?:killed|terminated)\b", re.IGNORECASE),
)


def resource_failure_reason(result: dict[str, Any]) -> str | None:
    returncode = int(result.get("returncode", 0))
    if returncode in {-9, 137}:
        return "process terminated by SIGKILL/exit 137"
    output = f"{result.get('stdout', '')}\n{result.get('stderr', '')}"
    for pattern in _RESOURCE_PATTERNS:
        if pattern.search(output):
            return "worker terminated due to memory/resource exhaustion"
    return None


def sequential_retry_command(command: str) -> str | None:
    try:
        arguments = shlex.split(command)
    except ValueError:
        return None
    if not arguments or "--runInBand" in arguments:
        return None

    executable = arguments[0].rsplit("/", 1)[-1]
    is_jest = executable == "jest" or (
        executable == "npx" and len(arguments) > 1 and arguments[1] == "jest"
    )
    is_npm_test = executable == "npm" and len(arguments) > 1 and (
        arguments[1] == "test"
        or (arguments[1] == "run" and len(arguments) > 2 and "test" in arguments[2])
    )
    if is_jest:
        return f"{command} --runInBand"
    if is_npm_test:
        separator = "" if "--" in arguments else " --"
        return f"{command}{separator} --runInBand"
    return None
