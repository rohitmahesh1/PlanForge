# server/create_db.py
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    config = Config(str(repo_root / "alembic.ini"))
    command.upgrade(config, "head")


if __name__ == "__main__":
    main()
