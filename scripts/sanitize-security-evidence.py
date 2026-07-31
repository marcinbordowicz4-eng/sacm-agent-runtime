#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

_REDACT_KEYS = {
    "code",
    "content",
    "match",
    "matchedcontent",
    "secret",
    "snippet",
    "text",
    "value",
}


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if str(key).lower().replace("_", "") in _REDACT_KEYS
                else sanitize(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Remove matched secret material from Trivy JSON/SARIF evidence."
    )
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args()
    for value in args.paths:
        path = Path(value)
        if not path.is_file():
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(
            json.dumps(sanitize(document), sort_keys=True, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
