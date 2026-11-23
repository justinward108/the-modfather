"""Text for votecounts, role PMs, claims and status. Pure functions over Game."""
from __future__ import annotations

from . import themes
from .catalog import AUTO_ABILITIES, NO_TARGET, TWO_TARGET
from .engine import DAY, DUSK, ENDED, NIGHT, NO_LYNCH, SIGNUPS, Game
from .timeparse import stamp

WIN_TEXT = {
    "town": "You win when every threat to the Town is dead.",
    "mafia": "You win when your faction makes up at least half of the living players "
             "and no other killers remain.",
    "third_party": "You have your own win condition — see your role description, "
                   "or ask the host if anything is unclear.",
}


def phase_name(g: Game) -> str:
    return {SIGNUPS: "Signups", DAY: f"Day {g.day}", DUSK: f"Day {g.day} (ended)",
            NIGHT: f"Night {g.day}", ENDED: "Game over"}[g.phase]


def deadline_line(g: Game) -> str:
    if g.phase == NIGHT and g.actions_closed:
        return "🌙 Night actions are closed — waiting on the host."
    if not g.deadline or g.phase not in (DAY, NIGHT):
        return ""
    what = f"Day {g.day} ends" if g.phase == DAY else f"Night {g.day} actions are due"
    return f"⏰ {what} {stamp(g.deadline, 'F')} ({stamp(g.deadline, 'R')})."


def missing_actions(g: Game) -> list[str]:
    """Living players / factions that haven't sent a night action yet."""
    out = [g.label(p) for p in g.living()
           if str(p.user_id) not in g.actions and g.role(p)["ability"] not in ("none",)
           and not _out_of_shots(g, p)]
    for fac in g.theme["factions"]:
        if fac["factional_kill"] and fac["name"] not in g.faction_kills and \
                any(g.slot(p)["faction"] == fac["name"] for p in g.living()):
            out.append(f"{fac['name']} (factional kill)")
    return out


def _out_of_shots(g: Game, p) -> bool:
    traits = g.role(p)["traits"]
    n = g.shots_allowed(traits)
    if n is not None and p.shots_used >= n:
        return True
    night = g.night_number()
    if "odd_night" in traits and night % 2 == 0:
        return True
    if "even_night" in traits and night % 2 == 1:
        return True
    return "day_action" in traits


def _count_lines(g: Game, snap: dict) -> list[str]:
    def char(slot):
        return f" ({g.theme['slots'][slot]['character']})" if slot is not None else ""
    lines = []
    for row in snap["rows"]:
        who = "No Lynch" if row["target_slot"] is None else row["target_name"] + char(row["target_slot"])
        names = ", ".join(v["name"] + (f" (x{v['weight']})" if v["weight"] != 1 else "") for v in row["voters"])
        lines.append(f"**{who}** [{row['count']}/{row['threshold']}]: {names}")
    lines = lines or ["*No votes.*"]
    if snap["not_voting"]:
        lines.append("Not voting: " + ", ".join(v["name"] for v in snap["not_voting"]))
    return lines


HOW = {"majority": "majority reached", "deadline": "the deadline passed",
       "host": "the host called it", "game ended": "the game ended"}


def votecount(g: Game, day: int | None = None) -> str:
    """Live count for the day in progress, or the final count of a finished day."""
    live = g.phase == DAY and (day is None or day == g.day)
    if live:
        snap = g.vote_snapshot()
        lines = [f"**Vote count — Day {g.day}**  ({snap['alive']} alive, {snap['majority']} to lynch)"]
        lines += _count_lines(g, snap)
        if g.locked:
            lines.append("🔒 *The day is locked.*")
        elif deadline_line(g):
            lines.append(deadline_line(g))
        return "\n".join(lines)
    if day is None:
        if not g.day_records:
            return "No day has finished yet."
        day = g.day_records[-1]["day"]
    rec = g.day_record(day)
    if rec is None:
        done = ", ".join(str(r["day"]) for r in g.day_records) or "none yet"
        raise_msg = f"There's no vote record for Day {day}. Finished days: {done}."
        return raise_msg
    lines = [f"**Final vote count — Day {day}**  ({rec['alive']} alive, {rec['majority']} to lynch)"]
    lines += _count_lines(g, rec)
    o = rec["outcome"]
    how = HOW.get(o.get("how", ""), "")
    if o.get("lynched_slot") is not None:
        who = f"{o['lynched_name']} ({g.theme['slots'][o['lynched_slot']]['character']})"
        lines.append(f"⚖️ **{who}** was lynched" + (" but survived" if o.get("survived") else "")
                     + (f" — {how}." if how else "."))
    else:
        lines.append("No one was lynched" + (f" — {how}." if how else "."))
    return "\n".join(lines)


def vote_history(g: Game, day: int) -> str:
    """Every vote and unvote on a day, in order, with Discord timestamps."""
    entries = [v for v in g.vote_log if v["day"] == day]
    if not entries:
        return f"No votes were cast on Day {day}."
    lines = [f"**Vote history — Day {day}**"]
    for v in entries:
        voter = v.get("voter_name") or f"<@{v['voter']}>"
        when = stamp(v["t"], "t") if v.get("t") else ""
        if v.get("target") is None:
            lines.append(f"{when} {voter} unvoted")
        else:
            tgt = v.get("target_name") or ("No Lynch" if v["target"] == NO_LYNCH else f"<@{v['target']}>")
            lines.append(f"{when} {voter} → **{tgt}**")
    rec = g.day_record(day)
    if rec and rec["outcome"].get("how") == "majority":
        lines.append("— majority reached —")
    return "\n".join(lines)


def vote_stats(g: Game, scope: str = "final", everything: bool = False) -> str:
    """Per-player table of votes on Mafia vs not Mafia."""
    rows = g.vote_stats(scope, everything)
    hidden = not everything and g.phase != ENDED
    title = "final vote of each day" if scope == "final" else "every vote cast"
    lines = [f"**Votes on Mafia vs not Mafia — {title}**"]
    if hidden:
        lines.append("*Only targets who have flipped count; votes on anyone still hidden are “?”.*")
    elif g.phase != ENDED:
        lines.append("*Host view — includes hidden factions. Don't share this mid-game.*")
    if scope == "final" and not g.day_records:
        lines.append("No day has finished yet.")
        return "\n".join(lines)
    name_w = 22
    table = [f"{'Player':<{name_w}} Mafia  Not  NL   ?   % on Mafia"]
    for st in rows:
        p = g.slot_holder(st["slot"])
        ch = g.theme["slots"][st["slot"]]["character"]
        nm = (p.name if p else "?") + (" †" if p and not p.alive else "")
        label = f"{nm} ({ch})"
        if len(label) > name_w:
            label = label[:name_w - 1] + "…"
        pct = f"{st['mafia_pct']}%" if st["mafia_pct"] is not None else "—"
        table.append(f"{label:<{name_w}} {st['mafia']:>5} {st['not_mafia']:>4} {st['no_lynch']:>3} {st['unknown']:>3}   {pct:>5}")
    lines.append("```\n" + "\n".join(table) + "\n```")
    replaced = [g.slot_holder(st["slot"]) for st in rows]
    notes = [f"{p.name} replaced {', '.join(f'<@{u}>' for u in p.replaced)}" for p in replaced if p and p.replaced]
    lines.append("% on Mafia = Mafia ÷ (Mafia + Not), so No Lynch and ? votes don't count. Third parties count as not Mafia. † = dead."
                 + (" Stats follow the character's slot: " + "; ".join(notes) + "." if notes else ""))
    return "\n".join(lines)


def teammates(g: Game, p) -> list[str]:
    fac = g.faction_of(p)
    if fac["kind"] == "town" and not fac["chat"]:
        return []
    if fac["kind"] == "third_party" and not fac["chat"]:
        return []
    return [f"{g.label(q)} — {g.role(q)['full_name']}" for q in g.players.values()
            if q.user_id != p.user_id and g.slot(q)["faction"] == g.slot(p)["faction"]]


def role_card(g: Game, p) -> dict:
    """Fields for a role PM embed: title, description, color, fields [(name, value)].

    If the GM wrote a PM for this character, it becomes the body of the card and the bot
    adds only the mechanical parts (how to act, teammates, vote weight). Otherwise the
    card is built from the role's starter text.
    """
    role, slot, fac = g.role(p), g.slot(p), g.faction_of(p)
    custom = themes.render_pm(g.theme, slot, f"<@{p.user_id}>").strip()
    fields = []
    if custom:
        body = custom
    else:
        body = f"**{g.theme['name']}**"
        fields += [("Role", role["full_name"]), ("Faction", slot["faction"]),
                   ("Ability", role["description"] or "*Your host will give you the details of this role.*")]
    ab = role["ability"]
    if ab == "manual":
        fields.append(("At night", "Send your action with `/action` (pick a target and add a note). "
                                   "The host resolves it."))
    elif ab not in NO_TARGET:
        how = "two targets" if ab in TWO_TARGET else "a target"
        fields.append(("At night", f"Use `/action` with {how}. ({AUTO_ABILITIES[ab]})"))
    elif ab == "commute":
        fields.append(("At night", "Use `/action` (no target needed) to commute."))
    if fac["factional_kill"]:
        fields.append(("Factional kill", "Any member can send the faction's kill with `/factionkill`. "
                                         "The last one sent is used, carried out by whoever sent it."))
    mates = teammates(g, p)
    if mates:
        fields.append(("Your faction", "\n".join(mates)))
    if not custom:
        fields.append(("Win condition", WIN_TEXT[fac["kind"]]))
    if role["vote_weight"] != 1:
        fields.append(("Vote", "You can't vote." if role["vote_weight"] == 0
                       else f"Your vote counts {role['vote_weight']} times."))
    return {"title": f"You are {g.character(p)}", "color": fac.get("color", "#888888"),
            "description": body[:4000], "fields": fields}


def claim_text(g: Game, c: dict) -> str:
    """'Dr. Melfi → Doctor', 'Doctor → Dr. Melfi', 'Tony / Godfather', with flags."""
    if c.get("character") and c.get("role"):
        text = f"{c['character']} / {c['role']}"
        if c.get("consistent") is False:
            text += " ⚠️*not a pair in this setup*"
    elif c.get("character"):
        text = c["character"] + (f" → {c['implied_role']}" if c.get("implied_role") else "")
    else:
        imp = c.get("implied_characters") or []
        text = c["role"] + (f" → {', '.join(imp)}" if imp and len(imp) <= 3 else "")
    unknown = [x for x, k in (("character", c.get("character_known")), ("role", c.get("role_known")))
               if k is False]
    if unknown and g.theme["settings"]["public_roster"]:
        text += f" ❓*{' & '.join(unknown)} not in setup*"
    if c.get("note"):
        text += f" — “{c['note']}”"
    return text


def counterclaim_lines(g: Game, only_uid: int | None = None) -> list[str]:
    cc = g.counterclaims()
    lines = []
    for ch, uids in cc["characters"].items():
        if only_uid is not None and only_uid not in uids:
            continue
        dead = next((p for p in g.players.values() if not p.alive and g.character(p) == ch), None)
        who = ", ".join(g.players[str(u)].name for u in uids)
        lines.append(f"⚔️ **{ch}** claimed by {who}" + (f" — but {dead.name} flipped as {ch}" if dead else ""))
    for role, (uids, copies) in cc["roles"].items():
        if only_uid is not None and only_uid not in uids:
            continue
        who = ", ".join(g.players[str(u)].name for u in uids)
        lines.append(f"⚔️ **{role}** claimed by {who} ({copies} left in setup)")
    return lines


def claims_board(g: Game, history: bool = False) -> str:
    lines = [f"**Claims — {phase_name(g)}**"]
    for p in sorted(g.players.values(), key=lambda q: (not q.alive, q.name.lower())):
        c = g.claims.get(str(p.user_id))
        status = "" if p.alive else f" ~~dead~~ (flipped {g.flip_text(p)})"
        if c:
            when = f"D{c['day']}" if c["phase"] in (DAY, DUSK) else f"N{c['day']}"
            lines.append(f"**{p.name}**{status}: {claim_text(g, c)} *({when})*")
        else:
            lines.append(f"**{p.name}**{status}: —")
    cc = counterclaim_lines(g)
    if cc:
        lines += ["", "**Counterclaims**"] + cc
    if history and g.claim_log:
        lines += ["", "**Claim history**"]
        for c in g.claim_log:
            when = f"D{c['day']}" if c["phase"] in (DAY, DUSK) else f"N{c['day']}"
            lines.append(f"{when} {c['name']}: " + ("*withdrew claim*" if c.get("withdrawn") else claim_text(g, c)))
    return "\n".join(lines)


def status(g: Game) -> str:
    lines = [f"**{g.theme['name']}** — {phase_name(g)}"]
    if g.phase == SIGNUPS:
        cap = g.max_players or len(g.theme["slots"])
        names = [g.signup_names.get(str(u), str(u)) for u in g.signups]
        lines.append(f"Signed up ({len(g.signups)}/{cap}): " + (", ".join(names) or "nobody yet"))
        counts = themes.supported_counts(g.theme)
        lines.append("Player counts this theme supports: " + (", ".join(map(str, counts)) or "none"))
    else:
        alive = g.living()
        lines.append(f"Alive ({len(alive)}): " + ", ".join(
            g.label(p) + (" ⚠️needs replacement" if p.needs_replacement else "") for p in alive))
        dead = [p for p in g.players.values() if not p.alive]
        if dead:
            lines.append("Dead: " + "; ".join(f"{p.name} — {g.flip_text(p)} ({p.death})" for p in dead))
    if deadline_line(g):
        lines.append(deadline_line(g))
    if g.subs:
        lines.append("Substitutes: " + ", ".join(g.sub_names.get(str(u), str(u)) for u in g.subs))
    if g.winners:
        lines.append("🏆 Winners: " + ", ".join(g.winners))
    return "\n".join(lines)


def roster(g: Game) -> str:
    if g.phase == SIGNUPS:
        return "**Possible roster**\n" + "\n".join(themes.roster_lines(g.theme))
    slots = sorted({p.slot for p in g.players.values()})
    lines = []
    for i in slots:
        s = g.theme["slots"][i]
        r = themes.slot_role(g.theme, s)
        lines.append(f"**{s['character']}** — {r['full_name']} ({s['faction']})")
    return "**Roster in play**\n" + "\n".join(lines)


def night_report(g: Game, report: dict) -> str:
    lines = [f"**Night {g.day} — preview** (nothing is applied until you press Apply)"]
    if report["deaths"]:
        lines.append("Deaths: " + ", ".join(f"{g.label(g.players[str(u)])}" for u, _ in report["deaths"]))
    else:
        lines.append("Deaths: none")
    lines.append("")
    lines += [f"• {x}" for x in report["log"]] or ["• No automatic actions."]
    if report["manual"]:
        lines.append("\n**Needs your ruling** (use `/host kill`, DM results yourself, etc.):")
        lines += [f"• {m['text']}" for m in report["manual"]]
    missing = missing_actions(g)
    if missing:
        lines.append("\nNo action from: " + ", ".join(missing))
    return "\n".join(lines)


def chunks(text: str, limit: int = 1900) -> list[str]:
    out, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit:
            out.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        out.append(cur)
    return out
