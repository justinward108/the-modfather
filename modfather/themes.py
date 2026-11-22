"""Theme (setup) schema: defaults, validation, and helpers shared by bot and dashboard."""
from __future__ import annotations

import copy
import re
import uuid

from . import catalog

FACTION_KINDS = {"town", "mafia", "third_party"}
REVEAL = {"role", "alignment", "none"}

DEFAULT_SETTINGS = {
    "reveal_on_death": "role",    # what is shown when someone dies
    "public_roster": True,        # players can see the full character/role list (open setup)
    "start_phase": "day",         # "day" = Day 1 first, "night" = Night 0 first
    "tie_rule": "no_lynch",       # plurality tie at /day end: "no_lynch" or "random"
    "allow_no_lynch": True,       # players may vote "No Lynch"
    "auto_end": True,             # end the game automatically when a win condition is hit
    "text_votes": True,           # parse "VOTE: name" / "UNVOTE" in the game channel
    "notify_blocked": False,      # tell roleblocked players they were blocked
    "day_hours": 48.0,            # default day length (0 = no deadline; host calls time)
    "night_hours": 24.0,          # default night action deadline after night starts (0 = none)
    "end_clock": "",              # move default deadlines to the nearest clock time, e.g. "20:00"
    "auto_night": True,           # start the night as soon as the day ends
    "reminder_hours": "24,1",     # reminders before a deadline, hours, comma separated
}

DEFAULT_FACTIONS = [
    {"name": "Town", "kind": "town", "color": "#4caf50", "chat": False,
     "factional_kill": False, "investigates_as": "innocent"},
    {"name": "Mafia", "kind": "mafia", "color": "#e53935", "chat": True,
     "factional_kill": True, "investigates_as": "guilty"},
    {"name": "Third Party", "kind": "third_party", "color": "#9c27b0", "chat": False,
     "factional_kill": False, "investigates_as": "guilty"},
]


class ThemeError(ValueError):
    pass


def new_theme(name: str = "Untitled theme") -> dict:
    return {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "description": "",
        "settings": dict(DEFAULT_SETTINGS),
        "factions": copy.deepcopy(DEFAULT_FACTIONS),
        "custom_roles": [],
        "slots": [],
    }


def _s(v, field, maxlen=200, required=False) -> str:
    if v is None:
        v = ""
    if not isinstance(v, str):
        raise ThemeError(f"{field} must be text")
    v = v.strip()
    if required and not v:
        raise ThemeError(f"{field} is required")
    if len(v) > maxlen:
        raise ThemeError(f"{field} is longer than {maxlen} characters")
    return v


def validate(raw: dict) -> dict:
    """Return a cleaned copy of a theme or raise ThemeError with a readable message."""
    if not isinstance(raw, dict):
        raise ThemeError("theme must be an object")
    t = new_theme()
    t["id"] = _s(raw.get("id") or t["id"], "id", 40, True)
    t["name"] = _s(raw.get("name"), "Theme name", 100, True)
    t["description"] = _s(raw.get("description"), "Description", 2000)

    settings = dict(DEFAULT_SETTINGS)
    for k, v in (raw.get("settings") or {}).items():
        if k not in DEFAULT_SETTINGS:
            continue
        default = DEFAULT_SETTINGS[k]
        if isinstance(default, bool):
            settings[k] = bool(v)
        elif isinstance(default, float):
            try:
                settings[k] = float(v or 0)
            except (TypeError, ValueError):
                raise ThemeError(f"{k.replace('_', ' ')} must be a number")
            if not 0 <= settings[k] <= 24 * 30:
                raise ThemeError(f"{k.replace('_', ' ')} must be between 0 and 720 hours")
        else:
            settings[k] = str(v if v is not None else "")
    if settings["reveal_on_death"] not in REVEAL:
        raise ThemeError("reveal_on_death must be role, alignment or none")
    if settings["start_phase"] not in ("day", "night"):
        raise ThemeError("start_phase must be day or night")
    if settings["tie_rule"] not in ("no_lynch", "random"):
        raise ThemeError("tie_rule must be no_lynch or random")
    clock = settings["end_clock"].strip()
    if clock:
        from .timeparse import _clock
        if _clock(clock) is None:
            raise ThemeError("Deadline clock time must look like 20:00 or 8pm")
    settings["end_clock"] = clock
    try:
        hrs = [float(x) for x in settings["reminder_hours"].replace(" ", "").split(",") if x]
    except ValueError:
        raise ThemeError("Reminders must be hours separated by commas, e.g. 24,1")
    if any(h <= 0 or h > 720 for h in hrs):
        raise ThemeError("Reminder hours must be between 0 and 720")
    settings["reminder_hours"] = ",".join(f"{h:g}" for h in sorted(set(hrs), reverse=True))
    t["settings"] = settings

    factions = []
    names = set()
    for i, f in enumerate(raw.get("factions") or []):
        name = _s(f.get("name"), f"Faction {i + 1} name", 50, True)
        if name.lower() in names:
            raise ThemeError(f"Duplicate faction name: {name}")
        names.add(name.lower())
        kind = f.get("kind", "town")
        if kind not in FACTION_KINDS:
            raise ThemeError(f"Faction {name}: kind must be town, mafia or third_party")
        color = f.get("color") or "#888888"
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            color = "#888888"
        inv = f.get("investigates_as") or ("innocent" if kind == "town" else "guilty")
        if inv not in ("innocent", "guilty"):
            inv = "guilty"
        factions.append({"name": name, "kind": kind, "color": color,
                         "chat": bool(f.get("chat")),
                         "factional_kill": bool(f.get("factional_kill")),
                         "investigates_as": inv})
    if not factions:
        raise ThemeError("A theme needs at least one faction")
    t["factions"] = factions

    mods = catalog.modifier_keys()
    customs = []
    ckeys = set()
    for i, r in enumerate(raw.get("custom_roles") or []):
        name = _s(r.get("name"), f"Custom role {i + 1} name", 60, True)
        key = r.get("key") or "custom_" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        key = _s(key, "Custom role key", 70, True)
        if not key.startswith("custom_"):
            key = "custom_" + key
        if key in ckeys:
            raise ThemeError(f"Two custom roles share the name {name}")
        ckeys.add(key)
        ability = r.get("ability") or "manual"
        if ability not in catalog.AUTO_ABILITIES:
            raise ThemeError(f"Custom role {name}: unknown ability {ability}")
        traits = [x for x in (r.get("traits") or []) if x in mods]
        customs.append({"key": key, "name": name,
                        "description": _s(r.get("description"), f"{name} description", 1500),
                        "ability": ability, "traits": traits,
                        "tags": ["Custom"]})
    t["custom_roles"] = customs

    slots = []
    for i, s in enumerate(raw.get("slots") or []):
        label = f"Slot {i + 1}"
        role_key = _s(s.get("role_key"), f"{label} role", 70, True)
        if catalog.get_role(role_key, customs) is None:
            raise ThemeError(f"{label}: unknown role '{role_key}'")
        faction = _s(s.get("faction"), f"{label} faction", 50, True)
        if faction.lower() not in names:
            raise ThemeError(f"{label}: faction '{faction}' doesn't exist")
        faction = next(f["name"] for f in factions if f["name"].lower() == faction.lower())
        vw = s.get("vote_weight")
        if vw in ("", None):
            vw = None
        else:
            try:
                vw = int(vw)
            except (TypeError, ValueError):
                raise ThemeError(f"{label}: vote weight must be a whole number")
            if not 0 <= vw <= 10:
                raise ThemeError(f"{label}: vote weight must be 0-10")
        mp = s.get("min_players") or 0
        try:
            mp = int(mp)
        except (TypeError, ValueError):
            raise ThemeError(f"{label}: 'only with N+ players' must be a whole number")
        if not 0 <= mp <= 100:
            raise ThemeError(f"{label}: 'only with N+ players' must be 0-100")
        slots.append({
            "min_players": mp,
            "character": _s(s.get("character"), f"{label} character name", 80, True),
            "role_key": role_key,
            "display_name": _s(s.get("display_name"), f"{label} role display name", 80),
            "faction": faction,
            "modifiers": [m for m in (s.get("modifiers") or []) if m in mods],
            "pm_text": _s(s.get("pm_text"), f"{label} role PM text", 3500),
            "vote_weight": vw,
        })
    chars = [s["character"].lower() for s in slots]
    dupes = {c for c in chars if chars.count(c) > 1}
    if dupes:
        raise ThemeError("Character names must be unique: " + ", ".join(sorted(dupes)))
    t["slots"] = slots
    return t


def faction(theme: dict, name: str) -> dict:
    for f in theme["factions"]:
        if f["name"] == name:
            return f
    return {"name": name, "kind": "town", "chat": False, "factional_kill": False,
            "investigates_as": "innocent", "color": "#888888"}


def slot_role(theme: dict, slot: dict) -> dict:
    """Effective role for a slot: catalog/custom role merged with slot overrides."""
    base = catalog.get_role(slot["role_key"], theme.get("custom_roles")) or {
        "key": slot["role_key"], "name": slot["role_key"], "description": "",
        "ability": "manual", "traits": [], "tags": []}
    traits = set(base.get("traits", [])) | set(slot.get("modifiers", []))
    weight = slot.get("vote_weight")
    if weight is None:
        weight = 3 if "vote_weight_3" in traits else 2 if "vote_weight_2" in traits else 1
    mod_names = {m["key"]: m["name"] for m in catalog.modifiers()}
    shown_mods = [mod_names[m] for m in slot.get("modifiers", []) if m in mod_names]
    name = slot.get("display_name") or base["name"]
    return {
        "key": base["key"],
        "base_name": base["name"],
        "name": name,
        "full_name": " ".join(shown_mods + [name]) if shown_mods else name,
        "description": base.get("description") or "",
        "confidence": base.get("confidence", "custom" if base.get("source") == "custom" else "none"),
        "pm_text": slot.get("pm_text") or "",
        "ability": base.get("ability", "manual"),
        "traits": traits,
        "vote_weight": weight,
        "tags": base.get("tags", []),
    }


def roster_lines(theme: dict) -> list[str]:
    out = []
    for s in theme["slots"]:
        r = slot_role(theme, s)
        out.append(f"**{s['character']}** — {r['full_name']} ({s['faction']})")
    return out


def select_slots(theme: dict, n: int) -> list[int]:
    """Pick which slot indexes are used for an n-player game.

    Core slots (min_players 0) are always used. Optional slots switch on once the game
    has at least min_players players. If that still leaves more slots than players, the
    optional slots with the highest thresholds are dropped first. Raises ThemeError if
    the theme can't seat exactly n players.
    """
    try:
        return select_slots_quiet(theme, n)
    except ThemeError:
        counts = supported_counts(theme)
        hint = ", ".join(map(str, counts)) if counts else "none - check the slot settings"
        raise ThemeError(f"This theme can't seat {n} players. Player counts it supports: {hint}.")


def supported_counts(theme: dict, upto: int = 60) -> list[int]:
    out = []
    for n in range(1, min(upto, len(theme["slots"])) + 1):
        try:
            select_slots_quiet(theme, n)
            out.append(n)
        except ThemeError:
            pass
    return out


def select_slots_quiet(theme: dict, n: int) -> list[int]:
    picked = [i for i, s in enumerate(theme["slots"]) if s.get("min_players", 0) <= n]
    optional = sorted((i for i in picked if theme["slots"][i].get("min_players", 0) > 0),
                      key=lambda i: (theme["slots"][i]["min_players"], i), reverse=True)
    while len(picked) > n and optional:
        picked.remove(optional.pop(0))
    if len(picked) != n:
        raise ThemeError("no fit")
    return picked


# ---------------------------------------------------------------- role PMs
PLACEHOLDERS = ("{character}", "{role}", "{faction}", "{player}")
GM_NOTE = "[GM:"

WIN_LINES = {
    "town": "You win when every threat to the Town is dead.",
    "mafia": "You win when the {faction} make up at least half of the living players and no other killers remain.",
}


def starter_pm(theme: dict, slot: dict) -> str:
    """A full draft role PM for the GM to edit. Uses placeholders so renames carry through."""
    r = slot_role(theme, slot)
    fac = faction(theme, slot["faction"])
    lines = ["You are **{character}**.",
             "[GM: a line or two about who {character} is in this story.]",
             "",
             "You are the **{role}**, aligned with the **{faction}**."]
    if r["description"]:
        lines.append(r["description"])
    else:
        lines.append("[GM: describe this role's ability.]")
    if r["confidence"] == "check":
        lines.append("[GM: this ability text is a best guess — confirm it matches your setup, then delete this line.]")
    if "vote_weight_2" in r["traits"] or "vote_weight_3" in r["traits"] or slot.get("vote_weight") not in (None, 1):
        pass  # the bot adds the vote weight to the card itself
    lines.append("")
    if fac["kind"] in WIN_LINES:
        lines.append(WIN_LINES[fac["kind"]])
    elif "win" in r["description"].lower():
        lines.append("[GM: confirm the win condition above.]")
    else:
        lines.append("[GM: write this role's win condition.]")
    return "\n".join(lines)


def render_pm(theme: dict, slot: dict, player_mention: str = "") -> str:
    r = slot_role(theme, slot)
    text = slot.get("pm_text") or ""
    for key, val in (("{character}", slot["character"]), ("{role}", r["full_name"]),
                     ("{faction}", slot["faction"]), ("{player}", player_mention)):
        text = text.replace(key, val)
    # GM notes are for the GM only: drop them (and any blank lines they leave doubled)
    lines = [ln for ln in text.split("\n") if GM_NOTE not in ln]
    out = []
    for ln in lines:
        if ln.strip() == "" and out and out[-1].strip() == "":
            continue
        out.append(ln)
    return "\n".join(out).strip()


def unfinished_pms(theme: dict, slot_ids: list[int] | None = None) -> list[str]:
    """Characters whose PM still contains a [GM: ...] note."""
    ids = range(len(theme["slots"])) if slot_ids is None else slot_ids
    return [theme["slots"][i]["character"] for i in ids if GM_NOTE in (theme["slots"][i].get("pm_text") or "")]
