"""SQLite persistence for themes, games and per-server settings."""
from __future__ import annotations

import json
import time

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS themes (
    guild_id INTEGER NOT NULL,
    id TEXT NOT NULL,
    data TEXT NOT NULL,
    updated REAL NOT NULL,
    PRIMARY KEY (guild_id, id)
);
CREATE TABLE IF NOT EXISTS games (
    id TEXT PRIMARY KEY,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    active INTEGER NOT NULL,
    data TEXT NOT NULL,
    updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS games_active ON games (active);
CREATE TABLE IF NOT EXISTS guild_config (
    guild_id INTEGER PRIMARY KEY,
    data TEXT NOT NULL
);
"""

DEFAULT_CONFIG = {"admin_role_id": None, "host_role_id": None, "timezone": None}


class Store:
    def __init__(self, path: str):
        self.path = path
        self.db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self.db = await aiosqlite.connect(self.path)
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript(SCHEMA)
        await self.db.commit()

    async def close(self) -> None:
        if self.db:
            await self.db.close()

    # themes
    async def list_themes(self, guild_id: int) -> list[dict]:
        cur = await self.db.execute(
            "SELECT data FROM themes WHERE guild_id=? ORDER BY updated DESC", (guild_id,))
        return [json.loads(r["data"]) for r in await cur.fetchall()]

    async def get_theme(self, guild_id: int, theme_id: str) -> dict | None:
        cur = await self.db.execute(
            "SELECT data FROM themes WHERE guild_id=? AND id=?", (guild_id, theme_id))
        row = await cur.fetchone()
        return json.loads(row["data"]) if row else None

    async def save_theme(self, guild_id: int, theme: dict) -> None:
        await self.db.execute(
            "INSERT INTO themes (guild_id, id, data, updated) VALUES (?,?,?,?) "
            "ON CONFLICT(guild_id, id) DO UPDATE SET data=excluded.data, updated=excluded.updated",
            (guild_id, theme["id"], json.dumps(theme), time.time()))
        await self.db.commit()

    async def delete_theme(self, guild_id: int, theme_id: str) -> None:
        await self.db.execute("DELETE FROM themes WHERE guild_id=? AND id=?", (guild_id, theme_id))
        await self.db.commit()

    # games
    async def save_game(self, game_dict: dict, active: bool) -> None:
        await self.db.execute(
            "INSERT INTO games (id, guild_id, channel_id, active, data, updated) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET active=excluded.active, data=excluded.data, "
            "updated=excluded.updated",
            (game_dict["id"], game_dict["guild_id"], game_dict["channel_id"], int(active),
             json.dumps(game_dict), time.time()))
        await self.db.commit()

    async def active_games(self) -> list[dict]:
        cur = await self.db.execute("SELECT data FROM games WHERE active=1")
        return [json.loads(r["data"]) for r in await cur.fetchall()]

    async def guild_games(self, guild_id: int, limit: int = 20) -> list[dict]:
        cur = await self.db.execute(
            "SELECT data FROM games WHERE guild_id=? ORDER BY updated DESC LIMIT ?", (guild_id, limit))
        return [json.loads(r["data"]) for r in await cur.fetchall()]

    # config
    async def get_config(self, guild_id: int) -> dict:
        cur = await self.db.execute("SELECT data FROM guild_config WHERE guild_id=?", (guild_id,))
        row = await cur.fetchone()
        return {**DEFAULT_CONFIG, **(json.loads(row["data"]) if row else {})}

    async def set_config(self, guild_id: int, cfg: dict) -> None:
        await self.db.execute(
            "INSERT INTO guild_config (guild_id, data) VALUES (?,?) "
            "ON CONFLICT(guild_id) DO UPDATE SET data=excluded.data", (guild_id, json.dumps(cfg)))
        await self.db.commit()
