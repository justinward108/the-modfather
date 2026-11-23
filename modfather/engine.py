"""Pure game logic. No Discord imports, so it can be unit tested and persisted as JSON.

Player ids are Discord user ids (ints). NO_LYNCH (0) is the vote target for "No Lynch".
"""
from __future__ import annotations

import random
import time
from dataclasses import asdict, dataclass, field

from . import themes
from .catalog import NO_TARGET, TWO_TARGET

NO_LYNCH = 0

SIGNUPS, DAY, DUSK, NIGHT, ENDED = "signups", "day", "dusk", "night", "ended"


class GameError(Exception):
    """A rule violation with a message safe to show to players."""


@dataclass
class Player:
    user_id: int
    name: str
    slot: int
    alive: bool = True
    death: str = ""              # e.g. "Lynched Day 2", "Killed Night 1", "Modkilled Day 3"
    shots_used: int = 0
    last_target: int | None = None
    needs_replacement: bool = False
    replaced: list[int] = field(default_factory=list)   # earlier occupants of this slot


@dataclass
class Game:
    id: str
    guild_id: int
    channel_id: int
    host_id: int
    theme: dict
    phase: str = SIGNUPS
    day: int = 0                         # Day N; the night after Day N is Night N
    max_players: int = 0
    signups: list[int] = field(default_factory=list)
    signup_names: dict = field(default_factory=dict)
    subs: list[int] = field(default_factory=list)       # substitute list
    sub_names: dict = field(default_factory=dict)
    players: dict = field(default_factory=dict)          # str(user_id) -> Player
    votes: dict = field(default_factory=dict)            # str(voter) -> target id
    vote_log: list = field(default_factory=list)        # every vote / unvote, with slots and names
    day_records: list = field(default_factory=list)      # final vote count + outcome of each finished day
    claims: dict = field(default_factory=dict)           # str(user_id) -> claim dict
    claim_log: list = field(default_factory=list)        # every claim/unclaim, in order
    actions: dict = field(default_factory=dict)          # str(user_id) -> action dict
    faction_kills: dict = field(default_factory=dict)    # faction -> {"actor", "target"}
    host_kills: list = field(default_factory=list)       # [uid, cause] applied at night's end, unblockable
    locked: bool = False
    deadline: float | None = None        # end of the current day / night-action deadline (unix time)
    actions_closed: bool = False         # night actions locked (deadline passed or host closed them)
    reminders_sent: list = field(default_factory=list)   # hours-before already announced this phase
    winners: list = field(default_factory=list)
    channels: dict = field(default_factory=dict)         # "category", "host", "dead", "faction:<name>"
    roles_ids: dict = field(default_factory=dict)        # "alive", "dead" Discord role ids
    history: list = field(default_factory=list)          # public event log
    created: float = field(default_factory=time.time)

    # ------------------------------------------------------------ persistence
    def to_dict(self) -> dict:
        d = asdict(self)
        d["players"] = {k: asdict(p) for k, p in self.players.items()}
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Game":
        d = dict(d)
        players = {k: Player(**p) for k, p in d.pop("players", {}).items()}
        g = cls(**d)
        g.players = players
        return g

    # ------------------------------------------------------------ lookups
    def player(self, uid: int) -> Player | None:
        return self.players.get(str(uid))

    def require_player(self, uid: int, alive: bool = True) -> Player:
        p = self.player(uid)
        if p is None:
            raise GameError("That person isn't in this game.")
        if alive and not p.alive:
            raise GameError(f"{p.name} is dead.")
        return p

    def living(self) -> list[Player]:
        return [p for p in self.players.values() if p.alive]

    def slot(self, p: Player) -> dict:
        return self.theme["slots"][p.slot]

    def role(self, p: Player) -> dict:
        return themes.slot_role(self.theme, self.slot(p))

    def faction_of(self, p: Player) -> dict:
        return themes.faction(self.theme, self.slot(p)["faction"])

    def character(self, p: Player) -> str:
        return self.slot(p)["character"]

    def label(self, p: Player) -> str:
        return f"{p.name} ({self.character(p)})"

    def find(self, text: str, alive_only: bool = True) -> Player | None:
        """Resolve free text (name, character, or mention) to a player."""
        t = text.strip().lower().lstrip("@")
        if t.startswith("<@") and t.endswith(">"):
            t = t.strip("<@!>")
        pool = self.living() if alive_only else list(self.players.values())
        for p in pool:
            if t == str(p.user_id):
                return p
        exact = [p for p in pool if t in (p.name.lower(), self.character(p).lower())]
        if len(exact) == 1:
            return exact[0]
        partial = [p for p in pool if p.name.lower().startswith(t)
                   or self.character(p).lower().startswith(t)]
        if len(partial) == 1:
            return partial[0]
        partial = [p for p in pool if t in p.name.lower() or t in self.character(p).lower()]
        return partial[0] if len(partial) == 1 else None

    def log(self, text: str) -> None:
        self.history.append({"t": time.time(), "day": self.day, "phase": self.phase, "text": text})

    # ------------------------------------------------------------ signups
    def join(self, uid: int, name: str) -> None:
        if self.phase != SIGNUPS:
            raise GameError("Signups are closed. Use /sub join to go on the substitute list.")
        if uid in self.signups:
            raise GameError("You're already signed up.")
        if self.max_players and len(self.signups) >= self.max_players:
            raise GameError("The game is full. Use /sub join to go on the substitute list.")
        self.signups.append(uid)
        self.signup_names[str(uid)] = name
        if uid in self.subs:
            self.subs.remove(uid)

    def leave(self, uid: int) -> None:
        if self.phase != SIGNUPS:
            raise GameError("The game has started; use /quit instead.")
        if uid not in self.signups:
            raise GameError("You aren't signed up.")
        self.signups.remove(uid)
        self.signup_names.pop(str(uid), None)

    def set_max_players(self, n: int) -> None:
        if self.phase != SIGNUPS:
            raise GameError("Player count can only change during signups.")
        counts = themes.supported_counts(self.theme)
        if n not in counts:
            raise GameError(f"This theme supports these player counts: {', '.join(map(str, counts))}.")
        if len(self.signups) > n:
            raise GameError(f"{len(self.signups)} people are already signed up; remove some first.")
        self.max_players = n

    def start(self, rng: random.Random | None = None) -> None:
        if self.phase != SIGNUPS:
            raise GameError("The game has already started.")
        rng = rng or random.Random()
        n = len(self.signups)
        try:
            slot_ids = themes.select_slots(self.theme, n)
        except themes.ThemeError as e:
            raise GameError(str(e))
        order = list(self.signups)
        rng.shuffle(order)
        self.players = {str(uid): Player(uid, self.signup_names.get(str(uid), str(uid)), s)
                        for uid, s in zip(order, slot_ids)}
        start = self.theme["settings"]["start_phase"]
        self.day = 1 if start == "day" else 0
        self.phase = DAY if start == "day" else NIGHT
        self.log(f"Game started with {n} players.")

    # ------------------------------------------------------------ subs / replacements / quitting
    def sub_join(self, uid: int, name: str) -> None:
        if self.phase == ENDED:
            raise GameError("This game is over.")
        if self.player(uid) and self.player(uid).alive:
            raise GameError("You're already playing.")
        if uid in self.subs:
            raise GameError("You're already on the substitute list.")
        if uid in self.signups and self.phase == SIGNUPS:
            raise GameError("You're signed up as a player.")
        self.subs.append(uid)
        self.sub_names[str(uid)] = name

    def sub_leave(self, uid: int) -> None:
        if uid not in self.subs:
            raise GameError("You're not on the substitute list.")
        self.subs.remove(uid)
        self.sub_names.pop(str(uid), None)

    def request_replacement(self, uid: int) -> Player:
        """Player quits or leaves the server: flag them; host replaces or modkills."""
        p = self.require_player(uid)
        p.needs_replacement = True
        self.log(f"{p.name} needs a replacement.")
        return p

    def replace(self, old_uid: int, new_uid: int, new_name: str) -> Player:
        """New user takes over a slot, keeping its role, state, votes and claim."""
        if self.phase in (SIGNUPS, ENDED):
            raise GameError("Replacements only happen while a game is running.")
        p = self.require_player(old_uid, alive=False)
        if not p.alive:
            raise GameError(f"{p.name} is dead; dead players aren't replaced.")
        if self.player(new_uid):
            raise GameError("That person is already in this game.")
        old_key, new_key = str(old_uid), str(new_uid)
        del self.players[old_key]
        p.replaced.append(old_uid)
        p.user_id, p.name, p.needs_replacement = new_uid, new_name, False
        self.players[new_key] = p
        if old_key in self.votes:
            self.votes[new_key] = self.votes.pop(old_key)
        for voter, target in list(self.votes.items()):
            if target == old_uid:
                self.votes[voter] = new_uid
        for store in (self.claims, self.actions):
            if old_key in store:
                store[new_key] = store.pop(old_key)
        for fk in self.faction_kills.values():
            if fk.get("actor") == old_uid:
                fk["actor"] = new_uid
            if fk.get("target") == old_uid:
                fk["target"] = new_uid
        for a in self.actions.values():
            a["targets"] = [new_uid if t == old_uid else t for t in a["targets"]]
        for q in self.players.values():
            if q.last_target == old_uid:
                q.last_target = new_uid
        if new_uid in self.subs:
            self.subs.remove(new_uid)
            self.sub_names.pop(new_key, None)
        self.log(f"{new_name} replaced {self.character(p)}'s previous player.")
        return p

    def kill(self, uid: int, cause: str) -> Player:
        """Remove a player from the game (night death, lynch, modkill, host kill)."""
        p = self.require_player(uid)
        p.alive = False
        p.death = cause
        p.needs_replacement = False
        key = str(uid)
        self.votes.pop(key, None)
        for voter, target in list(self.votes.items()):
            if target == uid:
                del self.votes[voter]
        self.actions.pop(key, None)
        for fac, fk in list(self.faction_kills.items()):
            if fk.get("actor") == uid or fk.get("target") == uid:
                del self.faction_kills[fac]
        self.log(f"{self.label(p)} — {cause}.")
        return p

    def revive(self, uid: int) -> Player:
        p = self.player(uid)
        if p is None:
            raise GameError("That person isn't in this game.")
        if p.alive:
            raise GameError(f"{p.name} is already alive.")
        p.alive, p.death = True, ""
        self.log(f"{self.label(p)} was revived by the host.")
        return p

    def flip_text(self, p: Player) -> str:
        reveal = self.theme["settings"]["reveal_on_death"]
        if reveal == "role":
            return f"{self.character(p)} — {self.role(p)['full_name']} ({self.slot(p)['faction']})"
        if reveal == "alignment":
            return f"{self.character(p)} ({self.slot(p)['faction']})"
        return self.character(p)

    # ------------------------------------------------------------ voting
    def weight(self, p: Player) -> int:
        return self.role(p)["vote_weight"]

    def majority(self) -> int:
        return len(self.living()) // 2 + 1

    def threshold(self, target: int) -> int:
        base = self.majority()
        if target == NO_LYNCH:
            return base
        traits = self.role(self.require_player(target))["traits"]
        if "hated" in traits:
            base -= 1
        if "loved" in traits:
            base += 1
        return max(1, base)

    def tally(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = {}
        for voter, target in self.votes.items():
            out.setdefault(target, []).append(int(voter))
        return out

    def votes_on(self, target: int) -> int:
        return sum(self.weight(self.players[str(v)]) for v in self.tally().get(target, []))

    def _check_open_day(self, uid: int) -> Player:
        if self.phase != DAY:
            raise GameError("Voting is only open during the day.")
        if self.locked:
            raise GameError("The day is locked.")
        p = self.require_player(uid)
        return p

    def vote(self, voter: int, target: int) -> int | None:
        """Cast or change a vote. Returns the lynched target id if a lynch is reached."""
        p = self._check_open_day(voter)
        if self.weight(p) == 0:
            raise GameError("Your role can't vote.")
        if target == NO_LYNCH:
            if not self.theme["settings"]["allow_no_lynch"]:
                raise GameError("No Lynch isn't allowed in this game.")
        else:
            self.require_player(target)
        self.votes[str(voter)] = target
        self._log_vote(p, target)
        if self.votes_on(target) >= self.threshold(target):
            return target
        return None

    def unvote(self, voter: int) -> None:
        self._check_open_day(voter)
        if str(voter) not in self.votes:
            raise GameError("You aren't voting for anyone.")
        del self.votes[str(voter)]
        self._log_vote(self.player(voter), None)

    def _log_vote(self, p: Player, target: int | None) -> None:
        tp = self.player(target) if target else None
        self.vote_log.append({
            "day": self.day, "voter": p.user_id, "voter_slot": p.slot, "voter_name": p.name,
            "target": target, "target_slot": tp.slot if tp else None,
            "target_name": tp.name if tp else ("No Lynch" if target == NO_LYNCH else None),
            "t": time.time()})

    def vote_snapshot(self) -> dict:
        """The vote count right now, with slots and names frozen for the record."""
        rows = []
        for target, voters in self.tally().items():
            tp = self.player(target) if target else None
            rows.append({
                "target": target, "target_slot": tp.slot if tp else None,
                "target_name": tp.name if tp else "No Lynch",
                "count": self.votes_on(target), "threshold": self.threshold(target),
                "voters": [{"uid": v, "slot": self.players[str(v)].slot, "name": self.players[str(v)].name,
                            "weight": self.weight(self.players[str(v)])} for v in voters]})
        rows.sort(key=lambda r: -r["count"])
        return {"day": self.day, "alive": len(self.living()), "majority": self.majority(), "rows": rows,
                "not_voting": [{"uid": p.user_id, "slot": p.slot, "name": p.name} for p in self.living()
                               if str(p.user_id) not in self.votes and self.weight(p) > 0],
                "t": time.time()}

    def record_day(self, outcome: dict) -> dict:
        rec = {**self.vote_snapshot(), "outcome": outcome}
        self.day_records = [r for r in self.day_records if r["day"] != self.day] + [rec]
        return rec

    def day_record(self, day: int) -> dict | None:
        return next((r for r in self.day_records if r["day"] == day), None)

    def plurality(self, rng: random.Random | None = None) -> int | None:
        """Day-end resolution: most-voted target, tie per theme setting. None = no lynch."""
        counts = {t: self.votes_on(t) for t in self.tally()}
        counts = {t: c for t, c in counts.items() if c > 0}
        if not counts:
            return None
        top = max(counts.values())
        leaders = [t for t, c in counts.items() if c == top]
        if len(leaders) > 1:
            if self.theme["settings"]["tie_rule"] == "random":
                return (rng or random).choice(leaders) or None
            return None
        return leaders[0] or None

    def lynch(self, target: int | None, how: str = "") -> dict:
        """Apply a lynch (or no lynch) and lock the day. Returns an outcome dict.

        how: "majority", "deadline", "host" — recorded with the day's final vote count.
        """
        if self.phase != DAY:
            raise GameError("There is no day in progress.")
        tp = self.player(target) if target else None
        self.record_day({"lynched": target or None, "lynched_slot": tp.slot if tp else None,
                         "lynched_name": tp.name if tp else None, "how": how,
                         "survived": bool(tp and "lynchproof" in self.role(tp)["traits"])})
        self.locked = True
        self.phase = DUSK
        self.deadline = None
        if not target:
            self.log(f"Day {self.day}: no lynch.")
            return {"lynched": None, "survived": False}
        p = self.require_player(target)
        if "lynchproof" in self.role(p)["traits"]:
            self.log(f"Day {self.day}: {p.name} was lynched but survived.")
            return {"lynched": p, "survived": True}
        self.kill(target, f"Lynched Day {self.day}")
        return {"lynched": p, "survived": False}

    def start_night(self) -> None:
        if self.phase not in (DAY, DUSK):
            raise GameError("Night can only start after a day.")
        self.phase = NIGHT
        self.locked = True
        self.votes = {}
        self.actions = {}
        self.faction_kills = {}
        self.host_kills = []
        self.deadline = None
        self.actions_closed = False
        self.reminders_sent = []

    def set_deadline(self, ts: float | None) -> None:
        if self.phase not in (DAY, NIGHT):
            raise GameError("Deadlines are set during a day or a night.")
        self.deadline = ts
        self.reminders_sent = []
        if self.phase == NIGHT and ts and ts > time.time():
            self.actions_closed = False

    def close_actions(self) -> None:
        if self.phase != NIGHT:
            raise GameError("It isn't night.")
        self.actions_closed = True

    def reopen_actions(self) -> None:
        if self.phase != NIGHT:
            raise GameError("It isn't night.")
        self.actions_closed = False

    def _check_actions_open(self) -> None:
        if self.phase != NIGHT:
            raise GameError("Night actions can only be sent at night.")
        if self.actions_closed:
            raise GameError("Night actions are closed for tonight. Ask the host if you need an exception.")

    def queue_host_kill(self, uid: int, cause: str = "") -> Player:
        if self.phase != NIGHT:
            raise GameError("Queued kills are for the night; outside the night the kill is immediate.")
        p = self.require_player(uid)
        self.host_kills = [hk for hk in self.host_kills if hk[0] != uid]
        self.host_kills.append([uid, cause or f"Killed Night {self.night_number()}"])
        return p

    def start_day(self) -> None:
        if self.phase != NIGHT:
            raise GameError("A day can only start after a night.")
        self.day += 1
        self.phase = DAY
        self.locked = False
        self.votes = {}
        self.deadline = None
        self.actions_closed = False
        self.reminders_sent = []

    # ------------------------------------------------------------ vote stats
    def slot_holder(self, slot: int) -> Player | None:
        return next((p for p in self.players.values() if p.slot == slot), None)

    def faction_known(self, slot: int, everything: bool) -> bool:
        """Can this slot's faction be shown? Always to the host / after the game;
        otherwise only once the player has died and flipped with their faction."""
        if everything or self.phase == ENDED:
            return True
        p = self.slot_holder(slot)
        return bool(p and not p.alive and self.theme["settings"]["reveal_on_death"] in ("role", "alignment"))

    def vote_stats(self, scope: str = "final", everything: bool = False) -> list[dict]:
        """Per player slot: how many of their votes landed on Mafia vs not Mafia.

        scope "final": the vote each player had when each finished day ended.
        scope "all": every vote cast (changes count separately; unvotes don't count).
        everything=False hides factions that aren't public yet (counted as unknown).
        "Mafia" means a faction of kind mafia; town and third parties are "not mafia".
        """
        votes: list[tuple[int, int | None, int]] = []      # (voter_slot, target_slot or None=no lynch, day)
        if scope == "all":
            for v in self.vote_log:
                if v.get("target") is None or v.get("voter_slot") is None:
                    continue
                votes.append((v["voter_slot"], v.get("target_slot"), v["day"]))
        else:
            for rec in self.day_records:
                for row in rec["rows"]:
                    for voter in row["voters"]:
                        votes.append((voter["slot"], row["target_slot"], rec["day"]))
        stats: dict[int, dict] = {}
        for p in self.players.values():
            stats[p.slot] = {"slot": p.slot, "mafia": 0, "not_mafia": 0, "no_lynch": 0, "unknown": 0}
        for voter_slot, target_slot, _day in votes:
            st = stats.setdefault(voter_slot, {"slot": voter_slot, "mafia": 0, "not_mafia": 0,
                                               "no_lynch": 0, "unknown": 0})
            if target_slot is None:
                st["no_lynch"] += 1
            elif not self.faction_known(target_slot, everything):
                st["unknown"] += 1
            elif themes.faction(self.theme, self.theme["slots"][target_slot]["faction"])["kind"] == "mafia":
                st["mafia"] += 1
            else:
                st["not_mafia"] += 1
        out = []
        for st in stats.values():
            judged = st["mafia"] + st["not_mafia"]
            st["total"] = judged + st["no_lynch"] + st["unknown"]
            st["mafia_pct"] = round(100 * st["mafia"] / judged) if judged else None
            out.append(st)
        out.sort(key=lambda s: (-(s["mafia_pct"] if s["mafia_pct"] is not None else -1), -s["total"]))
        return out

    # ------------------------------------------------------------ claims
    def slots_in_play(self) -> list[dict]:
        if self.players:
            return [self.theme["slots"][i] for i in sorted({p.slot for p in self.players.values()})]
        return list(self.theme["slots"])

    def _match(self, text: str, options: list[str]) -> str | None:
        t = text.strip().lower()
        if not t:
            return None
        exact = [o for o in options if o.lower() == t]
        if exact:
            return exact[0]
        pre = sorted({o for o in options if o.lower().startswith(t)})
        return pre[0] if len(pre) == 1 else None

    def _slot_role_name(self, slot: dict) -> str:
        return themes.slot_role(self.theme, slot)["name"]

    def claim(self, uid: int, character: str = "", role: str = "", note: str = "") -> dict:
        """Record a public claim: character only, role only, or both.

        Names are matched to the theme where possible. Using the theme's character/role
        pairs, a character-only claim implies that character's role and a role-only claim
        implies the character(s) holding it. A claim of both that doesn't match a pair in
        play is marked inconsistent (it may be a fake claim, or a closed setup).
        """
        p = self.require_player(uid)
        if self.phase in (SIGNUPS, ENDED):
            raise GameError("Claims are only made during a game.")
        character, role, note = character.strip()[:80], role.strip()[:80], note.strip()[:300]
        if not (character or role):
            raise GameError("Claim a character, a role, or both.")
        slots = self.slots_in_play()
        all_slots = self.theme["slots"]
        char_names = [s["character"] for s in all_slots]
        role_names = sorted({self._slot_role_name(s) for s in all_slots} |
                            {themes.slot_role(self.theme, s)["base_name"] for s in all_slots})
        char_c = self._match(character, char_names) if character else None
        role_c = self._match(role, role_names) if role else None
        if role_c:  # normalise base names to the name the setup shows
            for s in all_slots:
                r = themes.slot_role(self.theme, s)
                if role_c in (r["name"], r["base_name"]):
                    role_c = r["name"]
                    break
        c = {"character": char_c or character, "role": role_c or role, "note": note,
             "character_known": bool(char_c) if character else None,
             "role_known": bool(role_c) if role else None,
             "implied_role": "", "implied_characters": [], "consistent": None,
             "day": self.day, "phase": self.phase, "t": time.time()}
        if char_c and not role:
            slot = next((s for s in slots if s["character"] == char_c), None)
            if slot:
                c["implied_role"] = self._slot_role_name(slot)
        if role_c and not character:
            c["implied_characters"] = [s["character"] for s in slots if self._slot_role_name(s) == role_c]
        if char_c and role_c:
            slot = next((s for s in slots if s["character"] == char_c), None)
            c["consistent"] = bool(slot) and self._slot_role_name(slot) == role_c
        self.claims[str(p.user_id)] = c
        self.claim_log.append({"user_id": p.user_id, "name": p.name, **c})
        self.log(f"{p.name} claimed " + " / ".join(x for x in (c["character"], c["role"]) if x))
        return c

    def unclaim(self, uid: int) -> None:
        p = self.require_player(uid, alive=False)
        if self.claims.pop(str(p.user_id), None) is None:
            raise GameError("You haven't claimed anything.")
        self.claim_log.append({"user_id": p.user_id, "name": p.name, "withdrawn": True,
                               "day": self.day, "phase": self.phase, "t": time.time()})

    def claimed_character(self, c: dict) -> str:
        if c.get("character"):
            return c["character"]
        if len(c.get("implied_characters", [])) == 1:
            return c["implied_characters"][0]
        return ""

    def counterclaims(self) -> dict:
        """Living players whose claims collide.

        {"characters": {name: [uid, ...]}, "roles": {name: ([uid, ...], copies_in_play)}}
        A character is contested when two living players claim it (directly, or via a
        role only that character has). A role is contested when more living players claim
        it than there are copies of it in play.
        """
        living = {str(p.user_id) for p in self.living()}
        chars: dict[str, list[int]] = {}
        roles: dict[str, list[int]] = {}
        for uid_s, c in self.claims.items():
            if uid_s not in living:
                continue
            ch = self.claimed_character(c)
            if ch:
                chars.setdefault(ch, []).append(int(uid_s))
            r = c.get("role") or c.get("implied_role")
            if r:
                roles.setdefault(r, []).append(int(uid_s))
        slots = self.slots_in_play()
        copies = {}
        for s in slots:
            n = self._slot_role_name(s)
            copies[n] = copies.get(n, 0) + 1
        # dead players' revealed slots use up copies too
        if self.theme["settings"]["reveal_on_death"] == "role":
            for p in self.players.values():
                if not p.alive:
                    n = self.role(p)["name"]
                    copies[n] = copies.get(n, 0) - 1
        out_c = {k: v for k, v in chars.items() if len(v) > 1}
        for p in self.players.values():   # a dead player's character is always revealed
            if not p.alive and chars.get(self.character(p)):
                out_c[self.character(p)] = chars[self.character(p)]
        out_r = {k: (v, max(copies.get(k, 0), 0)) for k, v in roles.items()
                 if len(v) > max(copies.get(k, 0), 0) and k in copies}
        return {"characters": out_c, "roles": out_r}

    # ------------------------------------------------------------ night actions
    def night_number(self) -> int:
        return self.day

    def shots_allowed(self, traits: set) -> int | None:
        for key, n in (("one_shot", 1), ("two_shot", 2), ("three_shot", 3)):
            if key in traits:
                return n
        return None

    def submit_action(self, uid: int, targets: list[int], note: str = "") -> dict:
        self._check_actions_open()
        p = self.require_player(uid)
        role = self.role(p)
        ability, traits = role["ability"], role["traits"]
        if ability == "none":
            raise GameError("Your role has no night action.")
        n = self.night_number()
        if "odd_night" in traits and n % 2 == 0:
            raise GameError("You can only act on odd nights.")
        if "even_night" in traits and n % 2 == 1:
            raise GameError("You can only act on even nights.")
        if "day_action" in traits:
            raise GameError("Your ability is used during the day; tell the host.")
        shots = self.shots_allowed(traits)
        if shots is not None and p.shots_used >= shots:
            raise GameError("You have no uses of your ability left.")
        if ability == "manual":
            for t in targets:
                self.require_player(t, alive=False)
        elif ability in NO_TARGET:
            targets = []
        else:
            need = 2 if ability in TWO_TARGET else 1
            if len(targets) != need:
                raise GameError(f"Your ability needs {need} target{'s' if need > 1 else ''}.")
            for t in targets:
                tp = self.require_player(t)
                if t == uid and "self_target" not in traits and ability not in TWO_TARGET:
                    raise GameError("You can't target yourself.")
                if "loyal" in traits and t != uid and \
                        self.slot(tp)["faction"] == self.slot(p)["faction"]:
                    raise GameError("Loyal: you can't target your own faction.")
            if "non_consecutive" in traits and p.last_target in targets:
                raise GameError("You can't target the same player two nights in a row.")
        a = {"ability": ability, "targets": targets, "note": note[:500], "t": time.time()}
        self.actions[str(uid)] = a
        return a

    def cancel_action(self, uid: int) -> None:
        self._check_actions_open()
        if self.actions.pop(str(uid), None) is None:
            raise GameError("You have no action queued.")

    def submit_faction_kill(self, uid: int, target: int | None) -> str:
        self._check_actions_open()
        p = self.require_player(uid)
        fac = self.faction_of(p)
        if not fac["factional_kill"]:
            raise GameError("Your faction has no factional kill.")
        if target is None:
            self.faction_kills.pop(fac["name"], None)
            return fac["name"]
        self.require_player(target)
        self.faction_kills[fac["name"]] = {"actor": uid, "target": target}
        return fac["name"]

    # ------------------------------------------------------------ night resolution
    def resolve_night(self) -> dict:
        """Work out what happens tonight without changing the game.

        Returns {"deaths": [(uid, cause)], "results": {uid: [text]}, "manual": [...],
        "log": [text], "blocked": [uid]}. Call apply_night() with it to commit.
        Order: commute -> jail/block -> bus -> redirect -> protect/guard -> kills ->
        investigations. Blocks resolve simultaneously (a blocked blocker's block lands).
        """
        acts = []
        for uid_s, a in self.actions.items():
            p = self.players.get(uid_s)
            if p and p.alive:
                acts.append({"actor": p.user_id, "ability": a["ability"],
                             "targets": list(a["targets"]), "note": a.get("note", ""),
                             "source": "role"})
        for fac, fk in self.faction_kills.items():
            actor = self.player(fk["actor"])
            if actor and actor.alive:
                strong = "strongman" in self.role(actor)["traits"]
                acts.append({"actor": actor.user_id, "ability": "strongkill" if strong else "kill",
                             "targets": [fk["target"]], "note": "", "source": f"faction:{fac}"})

        log, manual = [], []
        results: dict[int, list[str]] = {}
        name = lambda uid: self.label(self.players[str(uid)])  # noqa: E731

        def traits(uid):
            return self.role(self.players[str(uid)])["traits"]

        # 1. commute
        commuters = {a["actor"] for a in acts if a["ability"] == "commute"}
        for c in commuters:
            log.append(f"{name(c)} commuted.")

        # 2. jail / block (simultaneous)
        blocked, jailed = set(), set()
        for a in acts:
            if a["ability"] in ("block", "jail") and a["targets"]:
                t = a["targets"][0]
                if t in commuters:
                    log.append(f"{name(a['actor'])}'s {a['ability']} on {name(t)} failed (commuted).")
                    continue
                if "roleblock_immune" in traits(t):
                    log.append(f"{name(t)} is roleblock immune; {name(a['actor'])}'s block failed.")
                else:
                    blocked.add(t)
                if a["ability"] == "jail":
                    jailed.add(t)
                log.append(f"{name(a['actor'])} {'jailed' if a['ability'] == 'jail' else 'blocked'} {name(t)}.")
        active = []
        for a in acts:
            if a["actor"] in blocked and a["ability"] not in ("block", "jail"):
                log.append(f"{name(a['actor'])}'s {a['ability']} was blocked.")
            else:
                active.append(a)

        # 3. bus drivers (applied in submission order)
        for b in [a for a in active if a["ability"] == "bus"]:
            x, y = b["targets"]
            if x in commuters or y in commuters:
                log.append(f"{name(b['actor'])}'s bus failed (a target commuted).")
                continue
            for a in active:
                if a is b:
                    continue
                a["targets"] = [y if t == x else x if t == y else t for t in a["targets"]]
            log.append(f"{name(b['actor'])} swapped {name(x)} and {name(y)}.")

        # 4. redirectors
        for r in [a for a in active if a["ability"] == "redirect"]:
            victim, new = r["targets"]
            if victim in commuters:
                continue
            for a in active:
                if a["actor"] == victim and a is not r and a["targets"]:
                    a["targets"] = [new for _ in a["targets"]]
                    log.append(f"{name(r['actor'])} redirected {name(victim)} to {name(new)}.")

        # targets that commuted make actions fail; record visits
        visits: list[tuple[int, int]] = []
        landed = []
        for a in active:
            if a["ability"] in ("commute", "none", "manual"):
                if a["ability"] == "manual":
                    manual.append(a)
                continue
            if any(t in commuters and t != a["actor"] for t in a["targets"]):
                log.append(f"{name(a['actor'])}'s {a['ability']} failed (target commuted).")
                continue
            landed.append(a)
            for t in a["targets"]:
                visits.append((a["actor"], t))

        # 5. protection
        protected: dict[int, list[int]] = {}
        guards: dict[int, list[int]] = {}
        for t in jailed:
            protected.setdefault(t, []).append(0)
        for a in landed:
            t = a["targets"][0] if a["targets"] else None
            if t is None:
                continue
            if a["ability"] == "protect":
                if "macho" in traits(t):
                    log.append(f"{name(a['actor'])}'s protection on {name(t)} failed (macho).")
                else:
                    protected.setdefault(t, []).append(a["actor"])
            elif a["ability"] == "guard":
                if "macho" in traits(t):
                    log.append(f"{name(a['actor'])}'s guard on {name(t)} failed (macho).")
                else:
                    guards.setdefault(t, []).append(a["actor"])

        # 6. kills
        dead: dict[int, str] = {}
        cause = f"Killed Night {self.night_number()}"
        for a in landed:
            if a["ability"] not in ("kill", "strongkill"):
                continue
            t = a["targets"][0]
            strong = a["ability"] == "strongkill" or "strongman" in traits(a["actor"])
            who = name(a["actor"]) + (f" [{a['source'][8:]} kill]" if a["source"] != "role" else "")
            if t in dead:
                log.append(f"{who} attacked {name(t)} (already dying).")
                continue
            live_guards = [g for g in guards.get(t, []) if g not in dead]
            if live_guards and not strong:
                g = live_guards[0]
                dead[g] = cause
                log.append(f"{who} attacked {name(t)}; bodyguard {name(g)} died instead.")
                continue
            if protected.get(t) and not strong:
                log.append(f"{who} attacked {name(t)}; they were protected.")
                continue
            if "bulletproof" in traits(t) and not strong:
                log.append(f"{who} attacked {name(t)}; they are bulletproof.")
                continue
            dead[t] = cause
            log.append(f"{who} killed {name(t)}.")

        # 7. investigations
        def guilty(t):
            tr = traits(t)
            if "suspicious" in tr:
                return True
            if "detect_immune" in tr:
                return False
            return self.faction_of(self.players[str(t)])["investigates_as"] == "guilty"

        for a in landed:
            ab, actor = a["ability"], a["actor"]
            if not ab.endswith("investigate") and ab not in ("track", "watch"):
                continue
            t = a["targets"][0]
            tname = name(t)
            if ab == "investigate":
                msg = f"{tname} is **{'guilty' if guilty(t) else 'innocent'}**."
            elif ab == "insane_investigate":
                msg = f"{tname} is **{'innocent' if guilty(t) else 'guilty'}**."
            elif ab == "naive_investigate":
                msg = f"{tname} is **innocent**."
            elif ab == "paranoid_investigate":
                msg = f"{tname} is **guilty**."
            elif ab == "role_investigate":
                msg = f"{tname} is a **{self.role(self.players[str(t)])['full_name']}**."
            elif ab == "track":
                if "ninja" in traits(t):
                    seen = []
                else:
                    seen = sorted({v for (s, v) in visits if s == t})
                msg = f"{tname} visited: " + (", ".join(name(v) for v in seen) or "nobody") + "."
            else:  # watch
                seen = sorted({s for (s, v) in visits if v == t and s != actor
                               and "ninja" not in traits(s)})
                msg = f"{tname} was visited by: " + (", ".join(name(v) for v in seen) or "nobody") + "."
            results.setdefault(actor, []).append(msg)
            log.append(f"{name(actor)} ({ab}): {msg}")

        if self.theme["settings"]["notify_blocked"]:
            for b in blocked:
                results.setdefault(b, []).append("You were roleblocked.")

        manual_out = [{"actor": a["actor"], "ability": "manual", "targets": a["targets"],
                       "note": a["note"],
                       "text": f"{name(a['actor'])} — {self.role(self.players[str(a['actor'])])['full_name']}: "
                               + (", ".join(name(t) for t in a["targets"]) or "no target")
                               + (f" — “{a['note']}”" if a["note"] else "")} for a in manual]
        for uid, hcause in self.host_kills:
            if self.player(uid) and self.player(uid).alive and uid not in dead:
                dead[uid] = hcause
                log.append(f"Host kill: {name(uid)}.")
        return {"deaths": list(dead.items()), "results": results, "manual": manual_out,
                "log": log, "blocked": sorted(blocked)}

    def apply_night(self, report: dict) -> list[Player]:
        """Commit a resolve_night() report: deaths, shot usage, non-consecutive memory."""
        if self.phase != NIGHT:
            raise GameError("It isn't night.")
        for uid_s, a in self.actions.items():
            p = self.players.get(uid_s)
            if not p:
                continue
            if a["ability"] not in ("none",):
                p.shots_used += 1
            p.last_target = a["targets"][0] if a["targets"] else None
        self.host_kills = []
        died = []
        for uid, cause in report["deaths"]:
            p = self.player(uid)
            if p and p.alive:
                died.append(self.kill(uid, cause))
        return died

    # ------------------------------------------------------------ win conditions
    def check_win(self) -> list[str] | None:
        """Return winning faction names (and solo winners as 'Name (Character)'), or None.

        Town wins when no Mafia-kind players and no killing third parties remain.
        A Mafia-kind faction wins when it has at least half the living players and no
        other killers remain. A lone killing third party wins at 1-v-1 or alone.
        """
        alive = self.living()
        if not alive:
            return ["Nobody"]

        def is_killer_tp(p):
            return self.faction_of(p)["kind"] == "third_party" and \
                self.role(p)["ability"] in ("kill", "strongkill")

        mafia_factions = {self.slot(p)["faction"] for p in alive if self.faction_of(p)["kind"] == "mafia"}
        killer_tps = [p for p in alive if is_killer_tp(p)]

        if not mafia_factions and not killer_tps:
            town = [f["name"] for f in self.theme["factions"] if f["kind"] == "town"]
            return town or ["Town"]
        if len(mafia_factions) == 1 and not killer_tps:
            fac = next(iter(mafia_factions))
            members = sum(1 for p in alive if self.slot(p)["faction"] == fac)
            if members * 2 >= len(alive):
                return [fac]
        if not mafia_factions and len(killer_tps) == 1 and len(alive) <= 2:
            return [self.label(killer_tps[0])]
        return None

    def end(self, winners: list[str]) -> None:
        if self.phase == DAY and not self.day_record(self.day):
            self.record_day({"lynched": None, "lynched_slot": None, "lynched_name": None,
                             "how": "game ended", "survived": False})
        self.phase = ENDED
        self.locked = True
        self.winners = winners
        self.log("Game over. Winners: " + ", ".join(winners))
