"""Apply packaged SACM database migrations."""

import os
from pathlib import Path

from alembic.config import Config

from alembic import command


def main() -> None:
    if not os.getenv("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL must be configured before running migrations.")
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    command.upgrade(config, "head")
