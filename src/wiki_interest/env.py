"""Load a local .env file (KEY=VALUE lines) into os.environ without overriding
variables that are already set. Standard library only: a dependency for twelve
lines of parsing is not worth it, and the skill must run with no .env at all.
Secrets never leave the process; nothing here logs values."""

from __future__ import annotations

import os
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent.parent


def load_dotenv(path: Path | None = None) -> int:
    """Returns the number of variables set. Silent if the file does not exist."""
    p = path or SKILL_ROOT / ".env"
    if not p.exists():
        return 0
    count = 0
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and value and key not in os.environ:
            os.environ[key] = value
            count += 1
    return count
