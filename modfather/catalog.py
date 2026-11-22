"""Role catalog: every Mafia Universe role plus per-guild custom roles."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "data" / "roles.json"

# Abilities the night resolver understands. "manual" = recorded for the host to rule on.
AUTO_ABILITIES = {
    "none": "No night action",
    "kill": "Kill a player",
    "strongkill": "Kill a player (ignores protection)",
    "protect": "Protect a player from kills",
    "guard": "Bodyguard a player (die in their place)",
    "block": "Roleblock a player",
    "jail": "Jail a player (block + protect)",
    "investigate": "Learn a player's alignment",
    "role_investigate": "Learn a player's role",
    "insane_investigate": "Alignment check, inverted results",
    "naive_investigate": "Alignment check, always innocent",
    "paranoid_investigate": "Alignment check, always guilty",
    "track": "Learn who a player visited",
    "watch": "Learn who visited a player",
    "bus": "Swap two players (2 targets)",
    "redirect": "Redirect a player's action (2 targets)",
    "commute": "Leave town; actions on you fail",
    "manual": "Host resolves by hand",
}
TWO_TARGET = {"bus", "redirect"}
NO_TARGET = {"commute", "none"}


@lru_cache(maxsize=1)
def _load() -> dict:
    with open(DATA, encoding="utf-8") as f:
        return json.load(f)


def mu_roles() -> list[dict]:
    return _load()["roles"]


def modifiers() -> list[dict]:
    return _load()["modifiers"]


def modifier_keys() -> set[str]:
    return {m["key"] for m in modifiers()}


@lru_cache(maxsize=1)
def _by_key() -> dict[str, dict]:
    return {r["key"]: r for r in mu_roles()}


def get_role(key: str, custom_roles: list[dict] | None = None) -> dict | None:
    """Look up a role by key, checking the theme's custom roles first."""
    for r in custom_roles or []:
        if r.get("key") == key:
            return {"tags": [], "traits": [], "alignment_hint": "any", "source": "custom", **r}
    return _by_key().get(key)


def search(query: str, custom_roles: list[dict] | None = None, limit: int = 25) -> list[dict]:
    q = query.lower().strip()
    pool = [get_role(r["key"], custom_roles) for r in (custom_roles or [])] + mu_roles()
    if not q:
        return pool[:limit]
    starts = [r for r in pool if r["name"].lower().startswith(q)]
    contains = [r for r in pool if q in r["name"].lower() and r not in starts]
    return (starts + contains)[:limit]
