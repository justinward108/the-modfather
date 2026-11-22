"""Settings from environment variables (loaded from .env if present)."""
from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(Path(os.environ.get("MODFATHER_ENV", ".env")))

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8080").rstrip("/")
WEB_HOST = os.environ.get("WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.environ.get("WEB_PORT", "8080"))
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
DB_PATH = os.environ.get("DB_PATH", "modfather.db")
# Optional: a server id to sync slash commands to instantly while testing.
DEV_GUILD_ID = int(os.environ["DEV_GUILD_ID"]) if os.environ.get("DEV_GUILD_ID") else None
# Text votes ("VOTE: name") need the privileged Message Content intent.
TEXT_VOTES = os.environ.get("TEXT_VOTES", "true").lower() in ("1", "true", "yes")
# Timezone used to read hosts' deadline times until a server sets its own with /mafia config.
DEFAULT_TIMEZONE = os.environ.get("DEFAULT_TIMEZONE", "UTC")
