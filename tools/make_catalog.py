"""Regenerate the catalog:  python tools/make_catalog.py tools/mu_roles.txt modfather/data/roles.json

Generate modfather/data/roles.json from the Mafia Universe role database list.

Role names and type tags come from the MU role database
(https://www.mafiauniverse.com/forums/database/mafia-roles/). Descriptions below are
written fresh for this bot and only cover roles whose standard meaning is well
established; every other role ships with an empty description that hosts fill in
from the dashboard (or the MU database) when they use it.
"""
import importlib.util, json, re, sys, unicodedata
from pathlib import Path

SRC, OUT = sys.argv[1], sys.argv[2]

DESC = {
 "Vanilla Townie": "No special ability. Find and lynch the threats to the Town.",
 "Mafia Goon": "No ability beyond the Mafia's shared night kill and private chat.",
 "Cop": "Each night, investigate one player and learn their alignment.",
 "Alignment Cop": "Each night, investigate one player and learn their alignment.",
 "Seer": "Each night, investigate one player and learn their alignment.",
 "Role Cop": "Each night, investigate one player and learn their role (and any modifiers).",
 "Insane Cop": "Investigates alignment, but every result is the opposite of the truth. You are not told this.",
 "Naive Cop": "Investigates alignment, but every result comes back innocent. You are not told this.",
 "Paranoid Cop": "Investigates alignment, but every result comes back guilty. You are not told this.",
 "Doctor": "Each night, protect one player from night kills.",
 "Healer": "Each night, protect one player from night kills.",
 "Weak Doctor": "Protects one player from night kills, but there is a chance the save fails.",
 "Faith Healer": "A Doctor whose protection only works part of the time.",
 "Bodyguard": "Each night, guard one player. If they are attacked, you die in their place.",
 "Roleblocker": "Each night, block one player from performing their night action.",
 "Jailkeeper": "Each night, jail one player: they are protected from kills and cannot act.",
 "Tracker": "Each night, follow one player and learn who they visited.",
 "Watcher": "Each night, watch one player and learn who visited them.",
 "Bus Driver": "Each night, pick two players. Every action aimed at one hits the other instead.",
 "Redirector": "Each night, pick a player and a new target; that player's action is redirected to the new target.",
 "Commuter": "Each night you may leave town; all actions targeting you fail.",
 "Strongman": "Your kill cannot be stopped by protection.",
 "Vigilante": "Each night, you may kill one player.",
 "Serial Killer": "A lone killer. Kill one player each night. You win when you are the last one standing.",
 "Godfather": "The Mafia leader. You appear innocent to alignment investigations.",
 "Super Godfather": "A Godfather variant; see the host for the exact immunities.",
 "Miller": "An innocent Townie who appears guilty to alignment investigations.",
 "Bulletproof": "You survive night kills.",
 "Deathproof": "You cannot die at night by any means.",
 "Lynchproof": "You cannot be lynched; if you are, you survive.",
 "Ninja": "Trackers and Watchers do not see your visits.",
 "Mason": "You know the other Masons and share a private chat. All Masons are confirmed Town to each other.",
 "Neighbor": "You share a private chat with one or more other players.",
 "Lover": "You are linked to another player. If one Lover dies, the other dies too.",
 "Jester": "Third party. You win if you get yourself lynched.",
 "Survivor": "Third party. You win if you are alive at the end of the game.",
 "Amnesiac": "Third party. Once during the game, pick a dead player and take their role.",
 "Arsonist": "Third party. Douse players at night, then ignite to kill everyone you've doused.",
 "Janitor": "When the Mafia kills someone you choose to clean, their role is hidden from everyone but you.",
 "Framer": "Each night, frame a player so they appear guilty to investigations that night.",
 "Lawyer": "Each night, pick a player; they appear innocent to investigations that night.",
 "Forger": "Change the role a player is shown as when they die.",
 "Silencer": "Each night, silence one player; they may not post during the next day.",
 "Doublevoter": "Your vote counts twice.",
 "Triplevoter": "Your vote counts three times.",
 "Hated": "It takes one fewer vote than normal to lynch you.",
 "Loved": "It takes one more vote than normal to lynch you.",
 "Desperado": "You may shoot a player at night; if they are Town, you die instead.",
 "Suicide Bomber": "Once, kill a target and yourself.",
 "Mortician": "Each night, learn the role of a dead player, even if it was hidden.",
 "Coroner": "Each night, examine a dead player and learn details about their death.",
 "Medium": "You can speak with the dead at night.",
 "Traitor": "A Town-appearing player who wins with the Mafia but doesn't know who they are.",
 "Cultist": "Member of the Cult. Win with your Cult.",
 "Cult Leader": "Third party. Each night, try to recruit a player into your Cult.",
 "Werewolf": "Member of the Werewolf faction; kills at night with the pack.",
 "Universal Backup": "When a power role dies, you may inherit it.",
 "Backup": "If the role you are backing up dies, you inherit it.",
 "Gunsmith": "Each night, give a player a gun they can use to shoot someone.",
 "Armorsmith": "Each night, give a player a vest that protects them from one kill.",
 "Saint": "If you are lynched, someone who voted for you dies.",
 "Virgin": "If you are lynched, the Mafia cannot kill the following night.",
 "Martyr": "Sacrifice yourself to save another player.",
 "Hider": "Hide behind a player at night. If they die, you die too.",
 "Hunter": "When you die, you may take someone with you.",
 "Mass Murderer": "Third party. Visit a house at night and kill everyone there.",
 "Babysitter": "Protect a player at night, but if you die, they die too.",
 "Oracle": "Choose a player each night; if you die, their role or alignment is revealed.",
 "Role Oracle": "Choose a player each night; if you die, their role is revealed publicly.",
 "Friendly Neighbor": "Each night, reveal yourself as Town to a player you pick.",
 "Paranoid Gun Owner": "You kill anyone who visits you at night.",
 "Governor": "Can overturn or force a lynch, typically once per game.",
}

# Abilities the night resolver handles automatically. Everything else is "manual":
# the bot records the action and the host rules on it.
ABILITY = {
 "Cop": "investigate", "Alignment Cop": "investigate", "Seer": "investigate",
 "Role Cop": "role_investigate",
 "Insane Cop": "insane_investigate", "Naive Cop": "naive_investigate",
 "Paranoid Cop": "paranoid_investigate",
 "Doctor": "protect", "Healer": "protect",
 "Bodyguard": "guard",
 "Roleblocker": "block",
 "Jailkeeper": "jail",
 "Tracker": "track", "Watcher": "watch",
 "Bus Driver": "bus", "Redirector": "redirect",
 "Commuter": "commute",
 "Vigilante": "kill", "Serial Killer": "kill", "Strongman": "strongkill",
 "Mafia Goon": "none", "Vanilla Townie": "none", "Miller": "none", "Godfather": "none",
 "Bulletproof": "none", "Ninja": "none", "Doublevoter": "none", "Triplevoter": "none",
 "Hated": "none", "Loved": "none", "Lynchproof": "none", "Mason": "none",
 "Jester": "none", "Survivor": "none", "Traitor": "none", "Cultist": "none",
}

# Passive traits the engine applies automatically.
TRAITS = {
 "Godfather": ["detect_immune"], "Miller": ["suspicious"],
 "Bulletproof": ["bulletproof"], "Ninja": ["ninja"], "Lynchproof": ["lynchproof"],
 "Doublevoter": ["vote_weight_2"], "Triplevoter": ["vote_weight_3"],
 "Hated": ["hated"], "Loved": ["loved"], "Strongman": ["strongman"],
}

MAFIA_HINT = {"Godfather", "Mafia Goon", "Janitor", "Framer", "Consigliere", "Forger",
              "Super Godfather", "Don", "Disguiser", "Yakuza", "Traitor", "Associate",
              "Hitman", "Saboteur", "Lawyer", "Silencer"}

def load_texts():
    texts = {}
    for f in sorted(Path(__file__).parent.glob("role_texts_*.py")):
        spec = importlib.util.spec_from_file_location(f.stem, f)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        dup = set(texts) & set(mod.TEXTS)
        assert not dup, f"duplicate texts: {dup}"
        texts.update(mod.TEXTS)
    return texts


TEXTS = load_texts()


def slug(name):
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", n.lower()).strip("_")

roles = []
for line in open(SRC, encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    name, tags = [p.strip() for p in line.split("|")]
    tags = [t.strip() for t in tags.split(",")]
    if "Third Party" in tags:
        hint = "third_party"
    elif name in MAFIA_HINT:
        hint = "mafia"
    else:
        hint = "any"
    roles.append({
        "key": slug(name), "name": name, "tags": tags,
        "description": TEXTS[name][1] if name in TEXTS else DESC.get(name, ""),
        "confidence": TEXTS[name][0] if name in TEXTS else ("standard" if name in DESC else "none"),
        "ability": ABILITY.get(name, "manual"),
        "traits": TRAITS.get(name, []),
        "alignment_hint": hint, "source": "mu",
    })

keys = [r["key"] for r in roles]
assert len(keys) == len(set(keys)), "duplicate keys"
names = {r["name"] for r in roles}
missing = [n for n in DESC if n not in names]
no_text = [r["name"] for r in roles if r["name"] not in TEXTS]
extra = [n for n in TEXTS if n not in names]
assert not extra, f"texts for unknown roles: {extra}"

MODIFIERS = [
 {"key": "bulletproof", "name": "Bulletproof", "description": "Survives night kills.", "auto": True},
 {"key": "ninja", "name": "Ninja", "description": "Visits are invisible to Trackers and Watchers.", "auto": True},
 {"key": "strongman", "name": "Strongman", "description": "Kills ignore protection.", "auto": True},
 {"key": "macho", "name": "Macho", "description": "Cannot be protected by Doctors or Bodyguards.", "auto": True},
 {"key": "detect_immune", "name": "Detection Immune", "description": "Appears innocent to alignment checks.", "auto": True},
 {"key": "suspicious", "name": "Suspicious", "description": "Appears guilty to alignment checks.", "auto": True},
 {"key": "roleblock_immune", "name": "Roleblock Immune", "description": "Cannot be roleblocked or jailed into inaction.", "auto": True},
 {"key": "lynchproof", "name": "Lynchproof", "description": "Survives being lynched.", "auto": True},
 {"key": "hated", "name": "Hated", "description": "Needs one fewer vote to lynch.", "auto": True},
 {"key": "loved", "name": "Loved", "description": "Needs one more vote to lynch.", "auto": True},
 {"key": "one_shot", "name": "1-Shot", "description": "Ability can be used once per game.", "auto": True},
 {"key": "two_shot", "name": "2-Shot", "description": "Ability can be used twice per game.", "auto": True},
 {"key": "three_shot", "name": "3-Shot", "description": "Ability can be used three times per game.", "auto": True},
 {"key": "odd_night", "name": "Odd Night", "description": "May only act on odd-numbered nights.", "auto": True},
 {"key": "even_night", "name": "Even Night", "description": "May only act on even-numbered nights.", "auto": True},
 {"key": "non_consecutive", "name": "Non-consecutive", "description": "Cannot target the same player two nights in a row.", "auto": True},
 {"key": "self_target", "name": "Self-targetable", "description": "May target themself.", "auto": True},
 {"key": "loyal", "name": "Loyal", "description": "Cannot target members of their own faction.", "auto": True},
 {"key": "day_action", "name": "Day Action", "description": "Uses the ability during the day (host resolves).", "auto": False},
 {"key": "compulsive", "name": "Compulsive", "description": "Must use the ability every night.", "auto": False},
]

json.dump({"source": "Mafia Universe role database (names and type tags)",
           "source_url": "https://www.mafiauniverse.com/forums/database/mafia-roles/",
           "roles": roles, "modifiers": MODIFIERS},
          open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(len(roles), "roles;", sum(1 for r in roles if r["description"]), "described;",
      sum(1 for r in roles if r["ability"] not in ("manual",)), "auto/none;",
      "DESC names not in list:", missing, "| roles without starter text:", no_text)
from collections import Counter
print("confidence:", dict(Counter(r["confidence"] for r in roles)))
