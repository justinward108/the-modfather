"""The Modfather: Discord commands and event handling. Game rules live in engine.py."""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid

import discord
from discord import app_commands
from discord.ext import commands

from . import config, render, themes, timeparse
from .engine import DAY, DUSK, ENDED, NIGHT, NO_LYNCH, SIGNUPS, Game, GameError

log = logging.getLogger("modfather")

VOTE_RE = re.compile(r"^\s*[*_~>]*\s*(un)?vote\b\s*:?\s*(.*?)\s*[*_~]*\s*$", re.I)
NO_LYNCH_WORDS = {"no lynch", "nolynch", "no-lynch", "no elimination", "noelim", "no elim", "nl"}


def color(hex_str: str) -> discord.Color:
    try:
        return discord.Color(int(hex_str.lstrip("#"), 16))
    except ValueError:
        return discord.Color.greyple()


def card_embed(card: dict) -> discord.Embed:
    e = discord.Embed(title=card["title"], description=card["description"], color=color(card["color"]))
    for name, value in card["fields"]:
        e.add_field(name=name, value=value[:1024], inline=name in ("Role", "Faction"))
    return e


async def reply(inter: discord.Interaction, content=None, *, embed=None, ephemeral=False, view=None):
    kwargs = {"content": content, "embed": embed, "ephemeral": ephemeral}
    if view is not None:
        kwargs["view"] = view
    if inter.response.is_done():
        return await inter.followup.send(**kwargs)
    return await inter.response.send_message(**kwargs)


class GameCog(commands.Cog):
    game = app_commands.Group(name="game", description="Create and run Mafia games", guild_only=True)
    host = app_commands.Group(name="host", description="Host controls for the current game", guild_only=True)
    sub = app_commands.Group(name="sub", description="Substitute list", guild_only=True)
    mafia = app_commands.Group(name="mafia", description="Bot settings", guild_only=True)

    def __init__(self, bot):
        self.bot = bot
        self.games: dict[str, Game] = {}
        self.locks: dict[str, asyncio.Lock] = {}

    async def cog_load(self):
        for d in await self.bot.store.active_games():
            g = Game.from_dict(d)
            self.games[g.id] = g
        log.info("Loaded %d active games", len(self.games))

    # ================================================================ helpers
    def lock(self, g: Game) -> asyncio.Lock:
        return self.locks.setdefault(g.id, asyncio.Lock())

    async def save(self, g: Game):
        await self.bot.store.save_game(g.to_dict(), active=g.phase != ENDED)
        if g.phase == ENDED:
            self.games.pop(g.id, None)

    def game_in_channel(self, channel_id: int) -> Game | None:
        for g in self.games.values():
            if g.channel_id == channel_id:
                return g
        for g in self.games.values():
            if channel_id in [v for k, v in g.channels.items() if k != "signup_msg"]:
                return g
        return None

    def game_for(self, inter: discord.Interaction) -> Game:
        g = self.game_in_channel(inter.channel_id)
        if g:
            return g
        mine = [g for g in self.games.values()
                if g.guild_id == inter.guild_id and g.player(inter.user.id)]
        if len(mine) == 1:
            return mine[0]
        raise GameError("There's no game in this channel. Run this in the game channel.")

    async def cfg(self, guild_id: int) -> dict:
        return await self.bot.store.get_config(guild_id)

    @staticmethod
    def setting(g: Game, key: str):
        return g.theme["settings"].get(key, themes.DEFAULT_SETTINGS[key])

    async def tz(self, g: Game) -> str:
        return (await self.cfg(g.guild_id)).get("timezone") or config.DEFAULT_TIMEZONE

    def reminder_hours(self, g: Game) -> list[float]:
        raw = str(self.setting(g, "reminder_hours") or "")
        out = []
        for x in raw.split(","):
            try:
                out.append(float(x))
            except ValueError:
                pass
        return sorted(out, reverse=True)

    def set_deadline(self, g: Game, ts: float | None, now: float | None = None):
        """Set the phase deadline and skip reminders whose time has already passed."""
        now = now or time.time()
        g.set_deadline(ts)
        if ts:
            g.reminders_sent = [h for h in self.reminder_hours(g) if ts - now <= h * 3600]

    async def apply_default_deadline(self, g: Game, now: float | None = None):
        now = now or time.time()
        key = "day_hours" if g.phase == DAY else "night_hours"
        ts = timeparse.default_deadline(now, float(self.setting(g, key) or 0),
                                        str(self.setting(g, "end_clock") or ""), await self.tz(g))
        self.set_deadline(g, ts, now)

    async def is_admin(self, member: discord.Member) -> bool:
        if member.guild_permissions.manage_guild or member.guild_permissions.administrator:
            return True
        rid = (await self.cfg(member.guild.id)).get("admin_role_id")
        return bool(rid and any(r.id == rid for r in member.roles))

    async def can_create(self, member: discord.Member) -> bool:
        if await self.is_admin(member):
            return True
        rid = (await self.cfg(member.guild.id)).get("host_role_id")
        return bool(rid and any(r.id == rid for r in member.roles))

    async def require_host(self, inter: discord.Interaction, g: Game):
        if inter.user.id == g.host_id or await self.is_admin(inter.user):
            return
        raise GameError("Only the host (or a server admin) can do that.")

    def channel(self, g: Game, key: str = "game") -> discord.abc.Messageable | None:
        cid = g.channel_id if key == "game" else g.channels.get(key)
        return self.bot.get_channel(cid) if cid else None

    async def post(self, g: Game, text: str, key: str = "game", **kw):
        ch = self.channel(g, key)
        if ch is None:
            return
        parts = render.chunks(text)
        for i, part in enumerate(parts):
            try:
                await ch.send(part, **(kw if i == len(parts) - 1 else {}))
            except discord.HTTPException as e:
                log.warning("post failed in %s: %s", key, e)

    async def tell_host(self, g: Game, text: str, **kw):
        if self.channel(g, "host"):
            await self.post(g, text, "host", **kw)
        else:
            user = self.bot.get_user(g.host_id)
            if user:
                try:
                    for part in render.chunks(text):
                        await user.send(part)
                except discord.HTTPException:
                    await self.post(g, f"<@{g.host_id}> (couldn't DM you) " + text)

    async def dm(self, user_id: int, content=None, embed=None) -> bool:
        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            await user.send(content=content, embed=embed)
            return True
        except discord.HTTPException:
            return False

    def guild(self, g: Game) -> discord.Guild | None:
        return self.bot.get_guild(g.guild_id)

    def drole(self, g: Game, key: str) -> discord.Role | None:
        gd = self.guild(g)
        rid = g.roles_ids.get(key)
        return gd.get_role(rid) if gd and rid else None

    # ================================================================ channels & permissions
    async def setup_channels(self, g: Game) -> list[str]:
        """Create Alive/Dead roles, a category with host / dead / faction chats. Returns problems."""
        gd = self.guild(g)
        problems = []
        try:
            short = g.theme["name"][:40]
            alive = await gd.create_role(name=f"{short} · Alive", mentionable=True,
                                         reason="Mafia game started")
            dead = await gd.create_role(name=f"{short} · Dead", reason="Mafia game started")
            g.roles_ids = {"alive": alive.id, "dead": dead.id}
        except discord.HTTPException as e:
            problems.append(f"I couldn't create roles ({e.__class__.__name__}; I need **Manage Roles**), "
                            "so channel locking will use per-player permissions instead.")
            alive = dead = None

        host_member = gd.get_member(g.host_id)
        hidden = {gd.default_role: discord.PermissionOverwrite(view_channel=False),
                  gd.me: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                     manage_channels=True, manage_messages=True)}
        if host_member:
            hidden[host_member] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        try:
            cat = await gd.create_category(f"{short} — game", overwrites=hidden)
            g.channels["category"] = cat.id
            hc = await gd.create_text_channel("host", category=cat)
            g.channels["host"] = hc.id
            dead_ow = dict(hidden)
            if dead:
                dead_ow[dead] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
            dc = await gd.create_text_channel("dead-chat", category=cat, overwrites=dead_ow)
            g.channels["dead"] = dc.id
            for fac in g.theme["factions"]:
                members = [p for p in g.players.values() if g.slot(p)["faction"] == fac["name"]]
                if not fac["chat"] or not members:
                    continue
                ow = dict(hidden)
                for p in members:
                    m = gd.get_member(p.user_id)
                    if m:
                        ow[m] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
                fc = await gd.create_text_channel(
                    re.sub(r"[^a-z0-9-]+", "-", fac["name"].lower()) + "-chat", category=cat, overwrites=ow)
                g.channels[f"faction:{fac['name']}"] = fc.id
                await fc.send(f"**{fac['name']} chat.** Your team:\n" + "\n".join(
                    f"• {g.label(p)} — {g.role(p)['full_name']}" for p in members))
        except discord.HTTPException as e:
            problems.append(f"I couldn't create channels ({e.__class__.__name__}; I need **Manage Channels**). "
                            "Some of the faction chats / dead chat / host channel may be missing.")
        for p in g.players.values():
            m = gd.get_member(p.user_id)
            if m and alive:
                try:
                    await m.add_roles(alive, reason="Mafia game")
                except discord.HTTPException:
                    problems.append(f"Couldn't give {p.name} the Alive role.")
        return problems

    async def set_speaking(self, g: Game, open_: bool):
        """Lock or unlock the game channel for living players."""
        ch = self.channel(g)
        gd = self.guild(g)
        if ch is None or gd is None:
            return
        try:
            await ch.set_permissions(gd.default_role, send_messages=False,
                                     reason="Mafia game in progress")
            alive, dead = self.drole(g, "alive"), self.drole(g, "dead")
            if alive:
                await ch.set_permissions(alive, send_messages=open_,
                                         reason="Day open" if open_ else "Day locked")
            else:
                for p in g.living():
                    m = gd.get_member(p.user_id)
                    if m:
                        await ch.set_permissions(m, send_messages=open_)
            if dead:
                await ch.set_permissions(dead, send_messages=False)
            host = gd.get_member(g.host_id)
            if host:
                await ch.set_permissions(host, send_messages=True)
        except discord.HTTPException:
            await self.tell_host(g, "⚠️ I couldn't change permissions in the game channel "
                                    "(need **Manage Channels** / **Manage Permissions** there).")

    async def release_channel(self, g: Game):
        ch = self.channel(g)
        gd = self.guild(g)
        if ch is None or gd is None:
            return
        try:
            await ch.set_permissions(gd.default_role, overwrite=None)
            for key in ("alive", "dead"):
                r = self.drole(g, key)
                if r:
                    await ch.set_permissions(r, overwrite=None)
            for p in g.players.values():
                m = gd.get_member(p.user_id)
                if m:
                    await ch.set_permissions(m, overwrite=None)
        except discord.HTTPException:
            pass

    async def mark_dead(self, g: Game, p):
        gd = self.guild(g)
        m = gd.get_member(p.user_id) if gd else None
        if not m:
            return
        alive, dead = self.drole(g, "alive"), self.drole(g, "dead")
        try:
            if alive:
                await m.remove_roles(alive)
            if dead:
                await m.add_roles(dead)
            if not alive:
                ch = self.channel(g)
                if ch:
                    await ch.set_permissions(m, send_messages=False)
            fc = self.channel(g, f"faction:{g.slot(p)['faction']}")
            if fc:
                await fc.set_permissions(m, view_channel=True, send_messages=False)
        except discord.HTTPException:
            pass

    # ================================================================ shared flows
    async def announce_deaths(self, g: Game, died: list, header: str):
        if not died:
            await self.post(g, f"{header}\nNobody died.")
            return
        lines = [header] + [f"💀 **{p.name}** has died. They were **{g.flip_text(p)}**." for p in died]
        await self.post(g, "\n".join(lines))
        for p in died:
            await self.mark_dead(g, p)

    async def maybe_end(self, g: Game) -> bool:
        winners = g.check_win()
        if not winners:
            return False
        if not g.theme["settings"]["auto_end"]:
            await self.tell_host(g, f"Win condition looks reached: **{', '.join(winners)}**. "
                                    "Use `/host end` to finish the game.")
            return False
        await self.finish(g, winners)
        return True

    async def finish(self, g: Game, winners: list[str]):
        g.end(winners)
        full = "\n".join(
            f"{'☠️' if not p.alive else '🙂'} **{p.name}** — {g.character(p)}: "
            f"{g.role(p)['full_name']} ({g.slot(p)['faction']})"
            + (f" *(replaced {', '.join(f'<@{u}>' for u in p.replaced)})*" if p.replaced else "")
            for p in sorted(g.players.values(), key=lambda q: g.slot(q)["faction"]))
        await self.post(g, f"🏁 **Game over!** Winners: **{', '.join(winners)}**\n\n{full}")
        await self.release_channel(g)
        await self.tell_host(g, "Game finished. Run `/game cleanup` to delete this game's "
                                "channels and roles when you're done with them.")

    async def do_lynch(self, g: Game, target: int | None, reason: str, how: str = "host"):
        out = g.lynch(target, how)
        await self.set_speaking(g, False)
        if out["lynched"] is None:
            await self.post(g, f"🔒 {reason}\n**No one was lynched.** The day is over.")
        elif out["survived"]:
            p = out["lynched"]
            await self.post(g, f"🔒 {reason}\n**{p.name}** was lynched… but survived!")
        else:
            p = out["lynched"]
            await self.post(g, f"🔒 {reason}\n⚖️ **{p.name}** has been lynched. "
                               f"They were **{g.flip_text(p)}**.")
            await self.mark_dead(g, p)
        if await self.maybe_end(g):
            return
        if self.setting(g, "auto_night"):
            await self.begin_night(g)
        else:
            await self.tell_host(g, f"Day {g.day} is over. Run `/host night` when you're ready.")

    async def handle_vote(self, g: Game, voter: int, target: int) -> str:
        lynched = g.vote(voter, target)
        p = g.player(voter)
        who = "No Lynch" if target == NO_LYNCH else g.players[str(target)].name
        if lynched is not None:
            reason = (f"**{p.name}** votes **{who}** — majority reached!")
            await self.do_lynch(g, None if lynched == NO_LYNCH else lynched, reason, "majority")
            return reason
        return f"**{p.name}** votes **{who}** ({g.votes_on(target)}/{g.threshold(target)})."

    async def begin_night(self, g: Game, deadline: float | None = None):
        g.start_night()
        if deadline:
            self.set_deadline(g, deadline)
        else:
            await self.apply_default_deadline(g)
        await self.set_speaking(g, False)
        due = render.deadline_line(g)
        await self.post(g, f"🌙 **Night {g.day} has begun.** The channel is locked. "
                           "Send night actions with `/action` (only you will see it)."
                           + (f"\n{due}" if due else ""))
        for fac in g.theme["factions"]:
            if fac["factional_kill"]:
                await self.post(g, f"🌙 Night {g.day}: send your kill with `/factionkill`."
                                + (f"\n{due}" if due else ""), f"faction:{fac['name']}")

    async def begin_day(self, g: Game, deadline: float | None = None):
        report = g.resolve_night()
        died = g.apply_night(report)
        undelivered = []
        for uid, msgs in report["results"].items():
            p = g.player(uid)
            text = f"**Night {g.day} result**\n" + "\n".join(msgs)
            if not await self.dm(uid, text):
                undelivered.append(f"{p.name if p else uid}: " + " ".join(msgs))
        if undelivered:
            await self.tell_host(g, "⚠️ Couldn't DM these results (DMs closed) — please pass them on:\n"
                                 + "\n".join(undelivered))
        await self.announce_deaths(g, died, f"☀️ **Night {g.day} is over.**")
        if await self.maybe_end(g):
            return
        g.start_day()
        if deadline:
            self.set_deadline(g, deadline)
        else:
            await self.apply_default_deadline(g)
        await self.set_speaking(g, True)
        alive = g.living()
        due = render.deadline_line(g)
        await self.post(g, f"☀️ **Day {g.day} has begun!** {len(alive)} players alive — "
                           f"{g.majority()} votes to lynch. Vote with `/vote` or `VOTE: name`."
                           + (f"\n{due}" if due else ""))

    async def announce_replacement_needed(self, g: Game, p, why: str):
        await self.post(g, f"⚠️ **{p.name}** ({g.character(p)}) {why}. A replacement is needed — "
                           "type `/sub join` to volunteer.")
        nxt = g.subs[0] if g.subs else None
        hint = (f" Next on the sub list: <@{nxt}> — `/host replace player:{p.name} new:@them`."
                if nxt else " The sub list is empty. You can also `/host modkill` them.")
        await self.tell_host(g, f"⚠️ {g.label(p)} {why}.{hint}")

    # ================================================================ autocomplete
    async def ac_living(self, inter, current: str, no_lynch=False, alive_only=True):
        try:
            g = self.game_for(inter)
        except GameError:
            return []
        cur = current.lower()
        out = []
        if no_lynch and g.theme["settings"]["allow_no_lynch"] and \
                ("no lynch".startswith(cur) or cur in NO_LYNCH_WORDS):
            out.append(app_commands.Choice(name="No Lynch", value="0"))
        pool = g.living() if alive_only else list(g.players.values())
        for p in pool:
            lab = g.label(p) + ("" if p.alive else " (dead)")
            if cur in lab.lower():
                out.append(app_commands.Choice(name=lab[:100], value=str(p.user_id)))
        return out[:25]

    async def ac_vote_target(self, inter, current):
        return await self.ac_living(inter, current, no_lynch=True)

    async def ac_alive(self, inter, current):
        return await self.ac_living(inter, current)

    async def ac_any_player(self, inter, current):
        return await self.ac_living(inter, current, alive_only=False)

    async def ac_theme(self, inter, current):
        out = []
        for t in await self.bot.store.list_themes(inter.guild_id):
            if current.lower() in t["name"].lower():
                out.append(app_commands.Choice(name=f"{t['name']} ({len(t['slots'])} slots)"[:100],
                                               value=t["id"]))
        return out[:25]

    async def ac_claim_char(self, inter, current):
        try:
            g = self.game_for(inter)
        except GameError:
            return []
        names = sorted({s["character"] for s in g.theme["slots"]})
        return [app_commands.Choice(name=n[:100], value=n[:100]) for n in names
                if current.lower() in n.lower()][:25]

    async def ac_claim_role(self, inter, current):
        try:
            g = self.game_for(inter)
        except GameError:
            return []
        names = sorted({themes.slot_role(g.theme, s)["name"] for s in g.theme["slots"]})
        out = [app_commands.Choice(name=n[:100], value=n[:100]) for n in names
               if current.lower() in n.lower()]
        if current and current not in names:
            out.insert(0, app_commands.Choice(name=current[:100], value=current[:100]))
        return out[:25]

    def target_id(self, g: Game, value: str, allow_no_lynch=False, alive=True) -> int:
        value = (value or "").strip()
        if allow_no_lynch and (value == "0" or value.lower() in NO_LYNCH_WORDS):
            return NO_LYNCH
        if value.isdigit() and g.player(int(value)):
            return int(value)
        p = g.find(value, alive_only=alive)
        if not p:
            raise GameError(f"I can't tell who “{value}” is. Pick from the list.")
        return p.user_id

    # ================================================================ /mafia
    @mafia.command(name="dashboard", description="Get the link to the theme builder")
    async def dashboard(self, inter: discord.Interaction):
        await reply(inter, f"Theme builder: {config.PUBLIC_URL}/  (log in with Discord; you need "
                           "Manage Server or the bot's admin role on this server)", ephemeral=True)

    @mafia.command(name="config", description="Set which roles can manage themes and host games")
    @app_commands.describe(admin_role="Can edit themes and control any game",
                           host_role="Can create and host games",
                           timezone="How hosts' deadline times are read, e.g. America/Chicago")
    async def mafia_config(self, inter: discord.Interaction, admin_role: discord.Role | None = None,
                           host_role: discord.Role | None = None, timezone: str | None = None):
        if not (inter.user.guild_permissions.manage_guild or inter.user.guild_permissions.administrator):
            raise GameError("You need Manage Server to change bot settings.")
        cfg = await self.cfg(inter.guild_id)
        if admin_role:
            cfg["admin_role_id"] = admin_role.id
        if host_role:
            cfg["host_role_id"] = host_role.id
        if timezone:
            timeparse.zone(timezone.strip())
            cfg["timezone"] = timezone.strip()
        await self.bot.store.set_config(inter.guild_id, cfg)
        a = f"<@&{cfg['admin_role_id']}>" if cfg["admin_role_id"] else "Manage Server only"
        h = f"<@&{cfg['host_role_id']}>" if cfg["host_role_id"] else "admins only"
        tz = cfg.get("timezone") or config.DEFAULT_TIMEZONE
        await reply(inter, f"Admin role: {a}\nHost role: {h}\nTimezone for deadlines: {tz}", ephemeral=True)

    # ================================================================ /game
    @game.command(name="new", description="Open signups for a themed game in this channel")
    @app_commands.describe(theme="A theme built in the dashboard",
                           players="How many players (must be a count the theme supports)")
    @app_commands.autocomplete(theme=ac_theme)
    async def game_new(self, inter: discord.Interaction, theme: str, players: int | None = None):
        if not await self.can_create(inter.user):
            raise GameError("You need the host role (or Manage Server) to start a game.")
        if self.game_in_channel(inter.channel_id):
            raise GameError("There's already a game running in this channel.")
        t = await self.bot.store.get_theme(inter.guild_id, theme)
        if not t:
            raise GameError("I can't find that theme. Pick one from the list.")
        t = themes.validate(t)
        counts = themes.supported_counts(t)
        if not counts:
            raise GameError("That theme can't seat any player count yet — check its slots in the dashboard.")
        g = Game(uuid.uuid4().hex[:10], inter.guild_id, inter.channel_id, inter.user.id, t)
        g.max_players = players or max(counts)
        if g.max_players not in counts:
            raise GameError(f"That theme supports these player counts: {', '.join(map(str, counts))}.")
        self.games[g.id] = g
        await reply(inter, embed=self.signup_embed(g), view=self.signup_view(g))
        msg = await inter.original_response()
        g.channels["signup_msg"] = msg.id
        await self.save(g)
        left = themes.unfinished_pms(t)
        if left:
            await inter.followup.send("⚠️ These characters' role PMs still have `[GM: …]` notes in them: "
                                      + ", ".join(left) + ". Edit them in the dashboard before `/game start`, "
                                      "— the notes will be left out of the DMs.", ephemeral=True)

    def signup_embed(self, g: Game) -> discord.Embed:
        e = discord.Embed(title=f"🎭 Signups: {g.theme['name']}",
                          description=g.theme.get("description") or "A new game of Mafia!",
                          color=discord.Color.dark_red())
        names = [f"<@{u}>" for u in g.signups]
        e.add_field(name=f"Players ({len(g.signups)}/{g.max_players})",
                    value="\n".join(names) or "Nobody yet", inline=True)
        if g.subs:
            e.add_field(name="Substitutes", value="\n".join(f"<@{u}>" for u in g.subs), inline=True)
        e.add_field(name="Host", value=f"<@{g.host_id}>", inline=True)
        e.set_footer(text="The Modfather")
        if g.theme["settings"]["public_roster"]:
            lines = themes.roster_lines(g.theme)
            e.add_field(name="Possible roster", value="\n".join(lines)[:1024] or "—", inline=False)
        return e

    def signup_view(self, g: Game) -> discord.ui.View:
        v = discord.ui.View(timeout=None)
        v.add_item(discord.ui.Button(label="Join", style=discord.ButtonStyle.success,
                                     custom_id=f"mafia:join:{g.id}"))
        v.add_item(discord.ui.Button(label="Leave", style=discord.ButtonStyle.secondary,
                                     custom_id=f"mafia:leave:{g.id}"))
        v.add_item(discord.ui.Button(label="Sub list", style=discord.ButtonStyle.primary,
                                     custom_id=f"mafia:sub:{g.id}"))
        return v

    async def refresh_signups(self, g: Game):
        ch = self.channel(g)
        mid = g.channels.get("signup_msg")
        if not ch or not mid:
            return
        try:
            msg = await ch.fetch_message(mid)
            await msg.edit(embed=self.signup_embed(g),
                           view=self.signup_view(g) if g.phase == SIGNUPS else None)
        except discord.HTTPException:
            pass

    @game.command(name="players", description="Change the player count during signups")
    async def game_players(self, inter: discord.Interaction, count: int):
        g = self.game_for(inter)
        await self.require_host(inter, g)
        async with self.lock(g):
            g.set_max_players(count)
            await self.save(g)
        await self.refresh_signups(g)
        await reply(inter, f"Player count set to {count}.")

    @game.command(name="add", description="Host: add someone to signups")
    async def game_add(self, inter: discord.Interaction, member: discord.Member):
        g = self.game_for(inter)
        await self.require_host(inter, g)
        async with self.lock(g):
            g.join(member.id, member.display_name)
            await self.save(g)
        await self.refresh_signups(g)
        await reply(inter, f"Added {member.mention}.", ephemeral=True)

    @game.command(name="remove", description="Host: remove someone from signups")
    async def game_remove(self, inter: discord.Interaction, member: discord.Member):
        g = self.game_for(inter)
        await self.require_host(inter, g)
        async with self.lock(g):
            g.leave(member.id)
            await self.save(g)
        await self.refresh_signups(g)
        await reply(inter, f"Removed {member.mention}.", ephemeral=True)

    @game.command(name="start", description="Host: assign roles and start the game")
    async def game_start(self, inter: discord.Interaction):
        g = self.game_for(inter)
        await self.require_host(inter, g)
        await inter.response.defer(thinking=True)
        async with self.lock(g):
            g.start()
            problems = await self.setup_channels(g)
            left = themes.unfinished_pms(g.theme, sorted({p.slot for p in g.players.values()}))
            if left:
                problems.append("These role PMs still had `[GM: …]` notes (left out of the DMs, so check "
                                "the PMs read right): " + ", ".join(left))
            failed = []
            for p in g.players.values():
                if not await self.dm(p.user_id, embed=card_embed(render.role_card(g, p))):
                    failed.append(p)
            await self.save(g)
        await self.refresh_signups(g)
        assign = "\n".join(f"• {g.label(p)} — {g.role(p)['full_name']} ({g.slot(p)['faction']})"
                           for p in g.players.values())
        await self.tell_host(g, f"**{g.theme['name']} — role assignments**\n{assign}")
        if problems:
            await self.tell_host(g, "⚠️ Setup problems:\n" + "\n".join(f"• {x}" for x in problems))
        text = (f"🎲 **{g.theme['name']} has begun** with {len(g.players)} players! "
                "Roles have been sent by DM — use `/role` any time to see yours privately.")
        if failed:
            text += "\n(" + ", ".join(f"<@{p.user_id}>" for p in failed) + \
                    ": I couldn't DM you. Use `/role` to see your role.)"
        if g.theme["settings"]["public_roster"]:
            text += "\n\n" + render.roster(g)
        await inter.followup.send(render.chunks(text)[0])
        for part in render.chunks(text)[1:]:
            await self.post(g, part)
        async with self.lock(g):
            if g.phase == DAY:
                await self.apply_default_deadline(g)
                await self.set_speaking(g, True)
                due = render.deadline_line(g)
                await self.post(g, f"☀️ **Day 1 has begun!** {g.majority()} votes to lynch."
                                + (f"\n{due}" if due else ""))
            else:
                g.phase = DAY  # begin_night expects to come from a day
                await self.begin_night(g)
            await self.save(g)

    @game.command(name="status", description="Phase, who's alive, who's dead")
    async def game_status(self, inter: discord.Interaction):
        g = self.game_for(inter)
        await reply(inter, render.chunks(render.status(g))[0])

    @game.command(name="roster", description="Show the characters and roles in this setup")
    async def game_roster(self, inter: discord.Interaction):
        g = self.game_for(inter)
        if not g.theme["settings"]["public_roster"] and not await self.is_admin(inter.user) \
                and inter.user.id != g.host_id:
            raise GameError("This is a closed setup; the roster is secret.")
        for i, part in enumerate(render.chunks(render.roster(g))):
            await reply(inter, part, ephemeral=True)

    @game.command(name="cancel", description="Host: cancel the game in this channel")
    async def game_cancel(self, inter: discord.Interaction):
        g = self.game_for(inter)
        await self.require_host(inter, g)
        async with self.lock(g):
            if g.phase == SIGNUPS:
                g.end(["Cancelled"])
                await self.refresh_signups(g)
                await reply(inter, "Signups cancelled.")
            else:
                await reply(inter, "Cancelling the game.")
                await self.finish(g, ["Cancelled — no winner"])
            await self.save(g)

    @game.command(name="cleanup", description="Delete the channels and roles made for the last game here")
    async def game_cleanup(self, inter: discord.Interaction):
        gd = inter.guild
        past = [Game.from_dict(d) for d in await self.bot.store.guild_games(gd.id)]
        g = next((x for x in past if x.channel_id == inter.channel_id and x.phase == ENDED), None)
        if not g:
            raise GameError("No finished game in this channel to clean up.")
        await self.require_host(inter, g)
        await inter.response.defer(ephemeral=True, thinking=True)
        removed = 0
        for key, cid in list(g.channels.items()):
            if key in ("signup_msg", "category"):
                continue
            ch = gd.get_channel(cid)
            if ch:
                try:
                    await ch.delete(reason="Mafia game cleanup")
                    removed += 1
                except discord.HTTPException:
                    pass
        cat = gd.get_channel(g.channels.get("category") or 0)
        if cat:
            try:
                await cat.delete(reason="Mafia game cleanup")
            except discord.HTTPException:
                pass
        for rid in g.roles_ids.values():
            r = gd.get_role(rid)
            if r:
                try:
                    await r.delete(reason="Mafia game cleanup")
                except discord.HTTPException:
                    pass
        g.channels = {k: v for k, v in g.channels.items() if k == "signup_msg"}
        g.roles_ids = {}
        await self.save(g)
        await inter.followup.send(f"Cleaned up {removed} channels and the game roles.", ephemeral=True)

    # ================================================================ player commands
    @app_commands.command(name="role", description="See your role privately")
    @app_commands.guild_only()
    async def role_cmd(self, inter: discord.Interaction):
        g = self.game_for(inter)
        p = g.player(inter.user.id)
        if not p:
            raise GameError("You're not in this game.")
        e = card_embed(render.role_card(g, p))
        if not p.alive:
            e.set_footer(text=f"You are dead ({p.death}).")
        await reply(inter, embed=e, ephemeral=True)

    @app_commands.command(name="vote", description="Vote to lynch a player")
    @app_commands.guild_only()
    @app_commands.autocomplete(target=ac_vote_target)
    async def vote_cmd(self, inter: discord.Interaction, target: str):
        g = self.game_for(inter)
        async with self.lock(g):
            tid = self.target_id(g, target, allow_no_lynch=True)
            msg = await self.handle_vote(g, inter.user.id, tid)
            await self.save(g)
        if g.phase == DAY:
            await reply(inter, msg)
        else:
            await reply(inter, "Vote recorded.", ephemeral=True)

    @app_commands.command(name="unvote", description="Remove your vote")
    @app_commands.guild_only()
    async def unvote_cmd(self, inter: discord.Interaction):
        g = self.game_for(inter)
        async with self.lock(g):
            g.unvote(inter.user.id)
            await self.save(g)
        await reply(inter, f"**{g.player(inter.user.id).name}** unvotes.")

    @app_commands.command(name="votecount", description="Vote count for today or any earlier day")
    @app_commands.guild_only()
    @app_commands.describe(day="Which day (default: today, or the last finished day at night)",
                           history="Also list every vote and unvote in order")
    async def votecount_cmd(self, inter: discord.Interaction, day: int | None = None, history: bool = False):
        g = self.game_for(inter)
        if g.phase == SIGNUPS:
            raise GameError("The game hasn't started.")
        if day is not None and not (1 <= day <= max(g.day, 1)):
            raise GameError(f"Pick a day from 1 to {max(g.day, 1)}.")
        text = render.votecount(g, day)
        if history:
            shown = day if day is not None else (g.day if g.phase == DAY or not g.day_records
                                                 else g.day_records[-1]["day"])
            text += "\n\n" + render.vote_history(g, shown)
        parts = render.chunks(text)
        await reply(inter, parts[0])
        for part in parts[1:]:
            await inter.followup.send(part)

    @app_commands.command(name="votestats", description="Each player's votes on Mafia vs not Mafia")
    @app_commands.guild_only()
    @app_commands.describe(scope="Final vote of each day (default) or every vote cast",
                           private="Host: see hidden factions privately mid-game (default on for the host)")
    @app_commands.choices(scope=[app_commands.Choice(name="Final vote of each day", value="final"),
                                 app_commands.Choice(name="Every vote cast", value="all")])
    async def votestats_cmd(self, inter: discord.Interaction, scope: str = "final", private: bool | None = None):
        g = self.game_for(inter)
        if g.phase == SIGNUPS:
            raise GameError("The game hasn't started.")
        is_host = inter.user.id == g.host_id or await self.is_admin(inter.user)
        if g.player(inter.user.id) and g.player(inter.user.id).alive and g.phase != ENDED:
            is_host = False      # a living player who is also an admin gets the public view
        everything = is_host and (private is not False) and g.phase != ENDED
        parts = render.chunks(render.vote_stats(g, scope, everything))
        for i, part in enumerate(parts):
            if i == 0:
                await reply(inter, part, ephemeral=everything)
            else:
                await inter.followup.send(part, ephemeral=everything)

    @app_commands.command(name="claim", description="Publicly claim a character, a role, or both")
    @app_commands.guild_only()
    @app_commands.describe(character="The character you claim to be", role="The role you claim",
                           note="Anything else (e.g. results)")
    @app_commands.autocomplete(character=ac_claim_char, role=ac_claim_role)
    async def claim_cmd(self, inter: discord.Interaction, character: str | None = None,
                        role: str | None = None, note: str | None = None):
        g = self.game_for(inter)
        async with self.lock(g):
            c = g.claim(inter.user.id, character or "", role or "", note or "")
            await self.save(g)
            name = g.player(inter.user.id).name
            mine = render.counterclaim_lines(g, inter.user.id)
        text = f"📣 **{name}** claims **{render.claim_text(g, c)}**"
        if mine:
            text += "\n" + "\n".join(mine)
        await reply(inter, text)

    @app_commands.command(name="unclaim", description="Withdraw your claim")
    @app_commands.guild_only()
    async def unclaim_cmd(self, inter: discord.Interaction):
        g = self.game_for(inter)
        async with self.lock(g):
            g.unclaim(inter.user.id)
            await self.save(g)
        await reply(inter, f"**{g.player(inter.user.id).name}** withdrew their claim.")

    @app_commands.command(name="claims", description="Show everyone's claims and counterclaims")
    @app_commands.guild_only()
    @app_commands.describe(history="Also list every claim change in order")
    async def claims_cmd(self, inter: discord.Interaction, history: bool = False):
        g = self.game_for(inter)
        parts = render.chunks(render.claims_board(g, history))
        await reply(inter, parts[0])
        for part in parts[1:]:
            await inter.followup.send(part)

    @app_commands.command(name="action", description="Send your night action (private)")
    @app_commands.guild_only()
    @app_commands.autocomplete(target=ac_alive, target2=ac_alive)
    async def action_cmd(self, inter: discord.Interaction, target: str | None = None,
                         target2: str | None = None, note: str | None = None):
        g = self.game_for(inter)
        async with self.lock(g):
            ids = [self.target_id(g, t) for t in (target, target2) if t]
            a = g.submit_action(inter.user.id, ids, note or "")
            await self.notify_if_all_in(g)
            await self.save(g)
        names = ", ".join(g.label(g.players[str(t)]) for t in a["targets"]) or "no target"
        await reply(inter, f"✅ Night action saved: **{names}**"
                           + (f" — “{a['note']}”" if a["note"] else "")
                           + ". You can change it until the night ends.", ephemeral=True)

    @app_commands.command(name="unaction", description="Cancel your night action")
    @app_commands.guild_only()
    async def unaction_cmd(self, inter: discord.Interaction):
        g = self.game_for(inter)
        async with self.lock(g):
            g.cancel_action(inter.user.id)
            await self.save(g)
        await reply(inter, "Night action cancelled.", ephemeral=True)

    @app_commands.command(name="factionkill", description="Send your faction's night kill")
    @app_commands.guild_only()
    @app_commands.autocomplete(target=ac_alive)
    async def factionkill_cmd(self, inter: discord.Interaction, target: str | None = None):
        g = self.game_for(inter)
        async with self.lock(g):
            tid = self.target_id(g, target) if target else None
            fac = g.submit_faction_kill(inter.user.id, tid)
            await self.notify_if_all_in(g)
            await self.save(g)
        p = g.player(inter.user.id)
        text = (f"🔪 {p.name} will kill **{g.label(g.players[str(tid)])}** tonight."
                if tid else f"{p.name} cancelled tonight's kill.")
        if self.channel(g, f"faction:{fac}") and inter.channel_id != g.channels.get(f"faction:{fac}"):
            await self.post(g, text, f"faction:{fac}")
        await reply(inter, text, ephemeral=inter.channel_id != g.channels.get(f"faction:{fac}"))

    @app_commands.command(name="quit", description="Leave the game you're in (asks for a replacement)")
    @app_commands.guild_only()
    async def quit_cmd(self, inter: discord.Interaction):
        g = self.game_for(inter)
        async with self.lock(g):
            if g.phase == SIGNUPS:
                g.leave(inter.user.id)
                await self.save(g)
                await self.refresh_signups(g)
                return await reply(inter, "You've left signups.", ephemeral=True)
            p = g.request_replacement(inter.user.id)
            await self.save(g)
        await reply(inter, "You've asked to leave. The host has been told; you'll stay in "
                           "until someone replaces you or the host removes you.", ephemeral=True)
        await self.announce_replacement_needed(g, p, "wants to leave the game")

    # ================================================================ /sub
    @sub.command(name="join", description="Volunteer as a substitute")
    async def sub_join(self, inter: discord.Interaction):
        g = self.game_for(inter)
        async with self.lock(g):
            g.sub_join(inter.user.id, inter.user.display_name)
            await self.save(g)
        await self.refresh_signups(g)
        await reply(inter, f"{inter.user.mention} is on the substitute list.")
        needing = [p for p in g.living() if p.needs_replacement]
        if needing:
            await self.tell_host(g, f"{inter.user.mention} joined the sub list. Waiting for a "
                                    f"replacement: {', '.join(g.label(p) for p in needing)}.")

    @sub.command(name="leave", description="Leave the substitute list")
    async def sub_leave(self, inter: discord.Interaction):
        g = self.game_for(inter)
        async with self.lock(g):
            g.sub_leave(inter.user.id)
            await self.save(g)
        await self.refresh_signups(g)
        await reply(inter, "You're off the substitute list.", ephemeral=True)

    @sub.command(name="list", description="Show the substitute list")
    async def sub_list(self, inter: discord.Interaction):
        g = self.game_for(inter)
        await reply(inter, "Substitutes: " + (", ".join(f"<@{u}>" for u in g.subs) or "none"),
                    ephemeral=True)

    # ================================================================ /host
    @host.command(name="night", description="End the day (if needed) and start the night")
    async def host_night(self, inter: discord.Interaction):
        g = self.game_for(inter)
        await self.require_host(inter, g)
        await inter.response.defer(ephemeral=True)
        async with self.lock(g):
            if g.phase == DAY:
                target = g.plurality()
                await self.do_lynch(g, target, f"**Day {g.day} has ended** (host called time).")
                if g.phase == ENDED:
                    await self.save(g)
                    return await inter.followup.send("Game over.", ephemeral=True)
            if g.phase != NIGHT:
                await self.begin_night(g)
            await self.save(g)
        await inter.followup.send("Night started. When everyone's in, use `/host resolve`.", ephemeral=True)

    @host.command(name="endday", description="End the day now (plurality vote, or force no lynch)")
    async def host_endday(self, inter: discord.Interaction, no_lynch: bool = False):
        g = self.game_for(inter)
        await self.require_host(inter, g)
        async with self.lock(g):
            if g.phase != DAY:
                raise GameError("There's no day in progress.")
            target = None if no_lynch else g.plurality()
            await reply(inter, "Ending the day.", ephemeral=True)
            await self.do_lynch(g, target, f"**Day {g.day} has ended** (host called time).")
            await self.save(g)

    @host.command(name="resolve", description="Preview tonight's results before applying them")
    async def host_resolve(self, inter: discord.Interaction):
        g = self.game_for(inter)
        await self.require_host(inter, g)
        if g.phase != NIGHT:
            raise GameError("It isn't night.")
        report = g.resolve_night()
        text = render.night_report(g, report)
        v = discord.ui.View(timeout=None)
        v.add_item(discord.ui.Button(label="Apply & start the day", style=discord.ButtonStyle.danger,
                                     custom_id=f"mafia:apply:{g.id}"))
        parts = render.chunks(text)
        for i, part in enumerate(parts):
            await reply(inter, part, ephemeral=True, view=v if i == len(parts) - 1 else None)

    @host.command(name="day", description="Apply the night's results and start the next day")
    @app_commands.describe(ends="When the day ends, e.g. 'Nov 24 8pm' or '48h' (default: the theme's day length)")
    async def host_day(self, inter: discord.Interaction, ends: str | None = None):
        g = self.game_for(inter)
        await self.require_host(inter, g)
        if g.phase != NIGHT:
            raise GameError("It isn't night.")
        ts = timeparse.parse_when(ends, time.time(), await self.tz(g)) if ends else None
        await inter.response.defer(ephemeral=True)
        async with self.lock(g):
            if g.phase != NIGHT:
                raise GameError("It isn't night.")
            await self.begin_day(g, ts)
            await self.save(g)
        await inter.followup.send("Done.", ephemeral=True)

    @host.command(name="actions", description="See the night actions sent so far")
    async def host_actions(self, inter: discord.Interaction):
        g = self.game_for(inter)
        await self.require_host(inter, g)
        if g.phase != NIGHT:
            raise GameError("It isn't night.")
        await reply(inter, render.chunks(render.night_report(g, g.resolve_night()))[0], ephemeral=True)

    @host.command(name="lynch", description="Force a lynch of a player")
    @app_commands.autocomplete(player=ac_alive)
    async def host_lynch(self, inter: discord.Interaction, player: str):
        g = self.game_for(inter)
        await self.require_host(inter, g)
        async with self.lock(g):
            if g.phase != DAY:
                raise GameError("There's no day in progress.")
            tid = self.target_id(g, player)
            await reply(inter, "Done.", ephemeral=True)
            await self.do_lynch(g, tid, "**The host has called the lynch.**")
            await self.save(g)

    @host.command(name="kill", description="Kill a player (now, or queued into tonight's deaths)")
    @app_commands.autocomplete(player=ac_alive)
    @app_commands.describe(cause="Shown in the death list, e.g. 'Killed Night 2'",
                           at_dawn="At night: reveal with tonight's deaths instead of announcing now")
    async def host_kill(self, inter: discord.Interaction, player: str, cause: str | None = None,
                        at_dawn: bool = False):
        g = self.game_for(inter)
        await self.require_host(inter, g)
        async with self.lock(g):
            tid = self.target_id(g, player)
            if at_dawn:
                p = g.queue_host_kill(tid, cause or "")
                await self.save(g)
                return await reply(inter, f"Queued: {g.label(p)} dies when the night resolves "
                                          "(this can't be blocked or protected).", ephemeral=True)
            p = g.kill(tid, cause or (f"Killed Night {g.day}" if g.phase == NIGHT else f"Killed Day {g.day}"))
            await reply(inter, "Done.", ephemeral=True)
            await self.post(g, f"💀 **{p.name}** has died. They were **{g.flip_text(p)}**.")
            await self.mark_dead(g, p)
            await self.maybe_end(g)
            await self.save(g)

    @host.command(name="modkill", description="Remove an inactive or rule-breaking player")
    @app_commands.autocomplete(player=ac_alive)
    async def host_modkill(self, inter: discord.Interaction, player: str, reason: str | None = None):
        g = self.game_for(inter)
        await self.require_host(inter, g)
        async with self.lock(g):
            tid = self.target_id(g, player)
            p = g.kill(tid, f"Modkilled {render.phase_name(g)}")
            await reply(inter, "Done.", ephemeral=True)
            await self.post(g, f"🚫 **{p.name}** has been modkilled" + (f" ({reason})" if reason else "")
                            + f". They were **{g.flip_text(p)}**.")
            await self.mark_dead(g, p)
            await self.maybe_end(g)
            await self.save(g)

    @host.command(name="revive", description="Bring a dead player back")
    @app_commands.autocomplete(player=ac_any_player)
    async def host_revive(self, inter: discord.Interaction, player: str):
        g = self.game_for(inter)
        await self.require_host(inter, g)
        async with self.lock(g):
            tid = self.target_id(g, player, alive=False)
            p = g.revive(tid)
            gd = self.guild(g)
            m = gd.get_member(tid) if gd else None
            if m:
                try:
                    if self.drole(g, "dead"):
                        await m.remove_roles(self.drole(g, "dead"))
                    if self.drole(g, "alive"):
                        await m.add_roles(self.drole(g, "alive"))
                    fc = self.channel(g, f"faction:{g.slot(p)['faction']}")
                    if fc:
                        await fc.set_permissions(m, view_channel=True, send_messages=True)
                except discord.HTTPException:
                    pass
            await self.save(g)
        await reply(inter, f"✨ **{p.name}** is alive again.")

    @host.command(name="replace", description="Swap a new person into a player's slot")
    @app_commands.autocomplete(player=ac_alive)
    async def host_replace(self, inter: discord.Interaction, player: str, new: discord.Member):
        g = self.game_for(inter)
        await self.require_host(inter, g)
        await inter.response.defer(ephemeral=True)
        async with self.lock(g):
            old_id = self.target_id(g, player)
            old_name = g.player(old_id).name
            p = g.replace(old_id, new.id, new.display_name)
            await self.save(g)
        gd = inter.guild
        old_m = gd.get_member(old_id)
        alive = self.drole(g, "alive")
        try:
            if old_m and alive:
                await old_m.remove_roles(alive)
            if alive:
                await new.add_roles(alive)
            ch = self.channel(g)
            if ch and not alive:
                if old_m:
                    await ch.set_permissions(old_m, overwrite=None)
                await ch.set_permissions(new, send_messages=g.phase == DAY and not g.locked)
            fc = self.channel(g, f"faction:{g.slot(p)['faction']}")
            if fc:
                if old_m:
                    await fc.set_permissions(old_m, overwrite=None)
                await fc.set_permissions(new, view_channel=True, send_messages=True)
                await fc.send(f"🔁 {new.mention} has replaced {old_name} as {g.character(p)}.")
        except discord.HTTPException:
            await self.tell_host(g, "⚠️ Couldn't update roles/permissions for the replacement.")
        ok = await self.dm(new.id, embed=card_embed(render.role_card(g, p)))
        history = ""
        if g.actions.get(str(new.id)):
            history = " Their night action carries over."
        await self.post(g, f"🔁 {new.mention} has replaced **{old_name}** as **{g.character(p)}**.")
        await inter.followup.send(f"Done.{history}" + ("" if ok else
                                  f" (Couldn't DM {new.display_name}; they can use `/role`.)"),
                                  ephemeral=True)

    @host.command(name="deadline", description="Set when the day ends / when night actions are due")
    @app_commands.describe(when="e.g. 'Nov 24 8pm', 'fri 9pm', '2026-11-24 20:00', '48h' — or 'none' to clear")
    async def host_deadline(self, inter: discord.Interaction, when: str):
        g = self.game_for(inter)
        await self.require_host(inter, g)
        if g.phase not in (DAY, NIGHT):
            raise GameError("Deadlines are set during a day or a night.")
        if when.strip().lower() in ("none", "clear", "off", "no"):
            ts = None
        else:
            ts = timeparse.parse_when(when, time.time(), await self.tz(g))
        async with self.lock(g):
            self.set_deadline(g, ts)
            await self.save(g)
        line = render.deadline_line(g) or ("⏰ No deadline — the host will call time." if g.phase == DAY
                                           else "⏰ No action deadline — the host will close actions.")
        await reply(inter, "Deadline updated.", ephemeral=True)
        await self.post(g, line)

    @host.command(name="extend", description="Push the current deadline back")
    @app_commands.describe(by="How long, e.g. '2h', '1d', '30m'")
    async def host_extend(self, inter: discord.Interaction, by: str):
        g = self.game_for(inter)
        await self.require_host(inter, g)
        secs = timeparse.parse_duration(by)
        if not secs:
            raise GameError("Give a length like `2h`, `1d` or `30m`.")
        if g.phase not in (DAY, NIGHT) or not g.deadline:
            raise GameError("There's no deadline to extend. Use `/host deadline`.")
        async with self.lock(g):
            base = max(g.deadline, time.time()) if g.phase == NIGHT and g.actions_closed else g.deadline
            self.set_deadline(g, base + secs)
            await self.save(g)
        await reply(inter, "Extended.", ephemeral=True)
        await self.post(g, f"⏳ Extended by {by.strip()}. " + render.deadline_line(g))

    @host.command(name="closeactions", description="Close night actions now (e.g. everyone has sent theirs)")
    async def host_closeactions(self, inter: discord.Interaction):
        g = self.game_for(inter)
        await self.require_host(inter, g)
        async with self.lock(g):
            g.close_actions()
            await self.save(g)
        await reply(inter, "Night actions closed.", ephemeral=True)
        await self.after_actions_closed(g, "closed by the host")

    @host.command(name="reopen", description="Reopen night actions (optionally with a new deadline)")
    @app_commands.describe(until="New action deadline, e.g. '1h' or '9pm' (optional)")
    async def host_reopen(self, inter: discord.Interaction, until: str | None = None):
        g = self.game_for(inter)
        await self.require_host(inter, g)
        ts = timeparse.parse_when(until, time.time(), await self.tz(g)) if until else None
        async with self.lock(g):
            g.reopen_actions()
            if ts:
                self.set_deadline(g, ts)
            elif g.deadline and g.deadline <= time.time():
                g.deadline = None          # past deadline would close it again immediately
            await self.save(g)
        await reply(inter, "Night actions reopened.", ephemeral=True)
        await self.post(g, "🌙 Night actions are open again. " + render.deadline_line(g))

    @host.command(name="lock", description="Lock the game channel")
    async def host_lock(self, inter: discord.Interaction):
        g = self.game_for(inter)
        await self.require_host(inter, g)
        async with self.lock(g):
            g.locked = True
            await self.set_speaking(g, False)
            await self.save(g)
        await reply(inter, "🔒 Channel locked.")

    @host.command(name="unlock", description="Unlock the game channel")
    async def host_unlock(self, inter: discord.Interaction):
        g = self.game_for(inter)
        await self.require_host(inter, g)
        async with self.lock(g):
            if g.phase == DUSK:
                g.phase = DAY   # reopen a day the host locked/lynched by mistake
            g.locked = False
            await self.set_speaking(g, True)
            await self.save(g)
        await reply(inter, "🔓 Channel unlocked.")

    @host.command(name="end", description="End the game and declare winners")
    async def host_end(self, inter: discord.Interaction, winners: str):
        g = self.game_for(inter)
        await self.require_host(inter, g)
        async with self.lock(g):
            await reply(inter, "Ending the game.", ephemeral=True)
            await self.finish(g, [w.strip() for w in winners.split(",") if w.strip()])
            await self.save(g)

    @host.command(name="transfer", description="Hand hosting to someone else")
    async def host_transfer(self, inter: discord.Interaction, new_host: discord.Member):
        g = self.game_for(inter)
        await self.require_host(inter, g)
        if g.player(new_host.id) and g.player(new_host.id).alive:
            raise GameError("A living player can't host.")
        async with self.lock(g):
            g.host_id = new_host.id
            await self.save(g)
        hc = self.channel(g, "host")
        if hc:
            try:
                await hc.set_permissions(new_host, view_channel=True, send_messages=True)
            except discord.HTTPException:
                pass
        await reply(inter, f"{new_host.mention} is now the host.")

    # ================================================================ deadlines
    async def after_actions_closed(self, g: Game, why: str):
        await self.post(g, f"🌙 Night {g.day} actions are closed ({why}). "
                           "Results will be posted when the host turns the night around.")
        for fac in g.theme["factions"]:
            if fac["factional_kill"]:
                await self.post(g, f"🌙 Actions are closed for Night {g.day}.", f"faction:{fac['name']}")
        v = discord.ui.View(timeout=None)
        v.add_item(discord.ui.Button(label="Apply & start the day", style=discord.ButtonStyle.danger,
                                     custom_id=f"mafia:apply:{g.id}"))
        await self.tell_host(g, f"<@{g.host_id}> Night {g.day} actions are closed ({why}). "
                                "Check the preview, handle anything marked for your ruling, then apply.\n\n"
                                + render.night_report(g, g.resolve_night()), view=v)

    async def notify_if_all_in(self, g: Game):
        if g.phase != NIGHT or "all_in" in g.reminders_sent:
            return
        if not render.missing_actions(g):
            g.reminders_sent.append("all_in")
            await self.tell_host(g, f"✅ Every Night {g.day} action is in. You can `/host closeactions` "
                                    "early or wait for the deadline.")

    async def tick(self, now: float | None = None):
        """Called every ~20s: reminders, day deadlines, night action deadlines."""
        now = now or time.time()
        for g in list(self.games.values()):
            if g.phase not in (DAY, NIGHT) or not g.deadline:
                continue
            async with self.lock(g):
                if g.phase not in (DAY, NIGHT) or not g.deadline:
                    continue
                changed = False
                left = g.deadline - now
                if left > 0 and not (g.phase == NIGHT and g.actions_closed):
                    for h in self.reminder_hours(g):
                        if h not in g.reminders_sent and left <= h * 3600:
                            g.reminders_sent.append(h)
                            changed = True
                            what = f"Day {g.day} ends" if g.phase == DAY else f"Night {g.day} actions are due"
                            await self.post(g, f"⏰ {what} {timeparse.stamp(g.deadline, 'R')} "
                                               f"({timeparse.stamp(g.deadline, 'f')}).")
                            if g.phase == NIGHT:
                                missing = render.missing_actions(g)
                                if missing:
                                    await self.tell_host(g, f"⏰ Night {g.day} actions due "
                                                            f"{timeparse.stamp(g.deadline, 'R')}. Still missing: "
                                                            + ", ".join(missing))
                            break   # one reminder per tick
                if left <= 0:
                    if g.phase == DAY:
                        target = g.plurality()
                        await self.do_lynch(g, target, f"⏰ **Day {g.day} has ended** — the deadline passed.",
                                            "deadline")
                        changed = True
                    elif g.phase == NIGHT and not g.actions_closed:
                        g.close_actions()
                        await self.after_actions_closed(g, "deadline passed")
                        changed = True
                if changed:
                    await self.save(g)

    async def run_scheduler(self, every: float = 20):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self.tick()
            except Exception:
                log.exception("scheduler tick failed")
            await asyncio.sleep(every)

    # ================================================================ events
    @commands.Cog.listener()
    async def on_interaction(self, inter: discord.Interaction):
        if inter.type != discord.InteractionType.component:
            return
        cid = (inter.data or {}).get("custom_id", "")
        if not cid.startswith("mafia:"):
            return
        _, action, gid = cid.split(":", 2)
        g = self.games.get(gid)
        try:
            if g is None:
                raise GameError("That game is no longer running.")
            if action in ("join", "leave", "sub"):
                async with self.lock(g):
                    if action == "join":
                        g.join(inter.user.id, inter.user.display_name)
                    elif action == "leave":
                        if inter.user.id in g.subs:
                            g.sub_leave(inter.user.id)
                        else:
                            g.leave(inter.user.id)
                    else:
                        g.sub_join(inter.user.id, inter.user.display_name)
                    await self.save(g)
                await inter.response.edit_message(embed=self.signup_embed(g), view=self.signup_view(g))
            elif action == "apply":
                await self.require_host(inter, g)
                await inter.response.defer(ephemeral=True)
                async with self.lock(g):
                    if g.phase != NIGHT:
                        raise GameError("This night has already been resolved.")
                    await self.begin_day(g)
                    await self.save(g)
                await inter.followup.send("Night applied.", ephemeral=True)
        except GameError as e:
            await reply(inter, str(e), ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message):
        if not config.TEXT_VOTES or msg.author.bot or not msg.guild:
            return
        g = self.game_in_channel(msg.channel.id)
        if not g or g.channel_id != msg.channel.id or g.phase != DAY or \
                not g.theme["settings"]["text_votes"]:
            return
        if not g.player(msg.author.id):
            return
        m = None
        for line in msg.content.splitlines():
            m = VOTE_RE.match(line)
            if m:
                break
        if not m:
            return
        try:
            async with self.lock(g):
                if m.group(1):
                    g.unvote(msg.author.id)
                    await self.save(g)
                    await msg.add_reaction("↩️")
                    return
                arg = m.group(2).strip().strip("*_ ")
                if msg.mentions:
                    tid = msg.mentions[0].id
                elif arg.lower() in NO_LYNCH_WORDS:
                    tid = NO_LYNCH
                else:
                    p = g.find(arg)
                    if not p:
                        await msg.reply(f"I couldn't match “{arg}” to a living player.", mention_author=False)
                        return
                    tid = p.user_id
                if tid != NO_LYNCH and not g.player(tid):
                    await msg.reply("That person isn't in the game.", mention_author=False)
                    return
                await self.handle_vote(g, msg.author.id, tid)
                await self.save(g)
            if g.phase == DAY:
                await msg.add_reaction("✅")
        except GameError as e:
            await msg.reply(str(e), mention_author=False)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        for g in list(self.games.values()):
            if g.guild_id != member.guild.id:
                continue
            async with self.lock(g):
                if g.phase == SIGNUPS and member.id in g.signups:
                    g.leave(member.id)
                    await self.save(g)
                    await self.refresh_signups(g)
                elif member.id in g.subs:
                    g.sub_leave(member.id)
                    await self.save(g)
                p = g.player(member.id)
                if p and p.alive and g.phase not in (SIGNUPS, ENDED):
                    g.request_replacement(member.id)
                    await self.save(g)
                    await self.announce_replacement_needed(g, p, "left the server")
                if member.id == g.host_id and g.phase != ENDED:
                    await self.post(g, "⚠️ The host left the server. A server admin can take over "
                                       "with `/host transfer`.")

    async def cog_app_command_error(self, inter: discord.Interaction, error: app_commands.AppCommandError):
        err = getattr(error, "original", error)
        if isinstance(err, GameError):
            msg = str(err)
        elif isinstance(err, themes.ThemeError):
            msg = f"Theme problem: {err}"
        elif isinstance(err, timeparse.TimeError):
            msg = str(err)
        elif isinstance(err, discord.Forbidden):
            msg = "I don't have permission to do that here. Check my role's permissions."
        else:
            log.exception("command failed", exc_info=err)
            msg = f"Something went wrong: {type(err).__name__}. The error was logged."
        try:
            await reply(inter, msg, ephemeral=True)
        except discord.HTTPException:
            pass
