"""Drive the real slash-command handlers through a whole game against fake Discord objects."""
import itertools
import random
from types import SimpleNamespace as NS

import discord
import pytest

from modfather import themes
from modfather.cog import GameCog
from modfather.engine import DAY, DUSK, ENDED, NIGHT
from modfather.store import Store

ids = itertools.count(1000)


class H(NS):
    """Hashable namespace (real discord Roles/Objects are hashable)."""
    __hash__ = object.__hash__
    __eq__ = object.__eq__


class FakeChannel:
    def __init__(self, name, guild):
        self.id, self.name, self.guild = next(ids), name, guild
        self.sent, self.perms, self.deleted = [], {}, False

    async def send(self, content=None, embed=None, view=None, **kw):
        self.sent.append(content if content is not None else f"<embed {embed.title if embed else ''}>")
        return NS(id=next(ids))

    async def set_permissions(self, target, overwrite=None, reason=None, **perms):
        self.perms[getattr(target, "id", target)] = perms or overwrite

    async def fetch_message(self, mid):
        async def edit(**kw):
            return None
        return NS(id=mid, edit=edit)

    async def delete(self, reason=None):
        self.deleted = True

    def text(self):
        return "\n".join(x for x in self.sent if x)


class FakeMember:
    __hash__ = object.__hash__

    def __init__(self, uid, name, guild, admin=False):
        self.id, self.display_name, self.name, self.guild = uid, name, name, guild
        self.mention = f"<@{uid}>"
        self.roles = []
        self.guild_permissions = NS(manage_guild=admin, administrator=admin)
        self.dms = []
        self.dm_open = True

    async def add_roles(self, *roles, reason=None):
        self.roles += [r for r in roles if r not in self.roles]

    async def remove_roles(self, *roles, reason=None):
        self.roles = [r for r in self.roles if r not in roles]

    async def send(self, content=None, embed=None):
        if not self.dm_open:
            raise discord.Forbidden(NS(status=403, reason="dm closed"), "closed")
        self.dms.append(content or (embed.title if embed else ""))


class FakeGuild:
    def __init__(self):
        self.id = 111
        self.members, self.roles_, self.channels = {}, {}, {}
        self.default_role = H(id=self.id, name="@everyone")
        self.me = H(id=1, name="bot")

    def add(self, uid, name, admin=False):
        m = FakeMember(uid, name, self, admin)
        self.members[uid] = m
        return m

    def get_member(self, uid):
        return self.members.get(uid)

    def get_role(self, rid):
        return self.roles_.get(rid)

    def get_channel(self, cid):
        return self.channels.get(cid)

    async def create_role(self, name, **kw):
        r = H(id=next(ids), name=name)

        async def delete(reason=None):
            self.roles_.pop(r.id, None)
        r.delete = delete
        self.roles_[r.id] = r
        return r

    async def create_category(self, name, overwrites=None):
        c = FakeChannel(name, self)
        self.channels[c.id] = c
        return c

    async def create_text_channel(self, name, category=None, overwrites=None):
        c = FakeChannel(name, self)
        c.overwrites = overwrites
        self.channels[c.id] = c
        return c


class FakeResponse:
    def __init__(self, inter):
        self.inter, self.done = inter, False

    def is_done(self):
        return self.done

    async def send_message(self, content=None, embed=None, ephemeral=False, view=None):
        self.done = True
        self.inter.out.append((content or (embed.title if embed else ""), ephemeral))

    async def defer(self, **kw):
        self.done = True

    async def edit_message(self, **kw):
        self.done = True
        self.inter.out.append(("<edited>", True))


class FakeInteraction:
    def __init__(self, user, channel, guild, data=None):
        self.user, self.guild, self.guild_id = user, guild, guild.id
        self.channel_id = channel.id
        self.out = []
        self.response = FakeResponse(self)
        self.data = data or {}
        self.type = discord.InteractionType.component if data else discord.InteractionType.application_command
        self.followup = NS(send=self._follow)

    async def _follow(self, content=None, embed=None, ephemeral=False, view=None):
        self.out.append((content or (embed.title if embed else ""), ephemeral))

    async def original_response(self):
        return NS(id=next(ids))

    def said(self):
        return "\n".join(str(c) for c, _ in self.out)


class FakeBot:
    def __init__(self, store, guild):
        self.store, self.guild_ = store, guild

    def get_channel(self, cid):
        return self.guild_.channels.get(cid)

    def get_guild(self, gid):
        return self.guild_ if gid == self.guild_.id else None

    def get_user(self, uid):
        return self.guild_.members.get(uid)

    async def fetch_user(self, uid):
        m = self.guild_.members.get(uid)
        if not m:
            raise discord.NotFound(NS(status=404, reason="x"), "no user")
        return m


THEME = {
    "name": "Sopranos", "factions": themes.DEFAULT_FACTIONS,
    "slots": [
        {"character": "Tony", "role_key": "godfather", "faction": "Mafia"},
        {"character": "Silvio", "role_key": "mafia_goon", "faction": "Mafia"},
        {"character": "Melfi", "role_key": "doctor", "faction": "Town"},
        {"character": "Harris", "role_key": "cop", "faction": "Town"},
        {"character": "Artie", "role_key": "vanilla_townie", "faction": "Town"},
        {"character": "Carmela", "role_key": "vanilla_townie", "faction": "Town"},
        {"character": "Furio", "role_key": "vigilante", "faction": "Town"},
        {"character": "Adriana", "role_key": "vanilla_townie", "faction": "Town", "min_players": 8},
    ]}


@pytest.fixture
async def world(tmp_path):
    store = Store(str(tmp_path / "db.sqlite"))
    await store.open()
    guild = FakeGuild()
    host = guild.add(10, "Host", admin=True)
    players = [guild.add(20 + i, f"P{i}") for i in range(8)]
    sub = guild.add(99, "Subby")
    game_ch = FakeChannel("mafia", guild)
    guild.channels[game_ch.id] = game_ch
    t = themes.validate({**THEME, "id": "sop"})
    await store.save_theme(guild.id, t)
    bot = FakeBot(store, guild)
    cog = GameCog(bot)
    await cog.cog_load()
    yield NS(store=store, guild=guild, host=host, players=players, sub=sub, ch=game_ch, bot=bot, cog=cog)
    await store.close()


async def run(cmd, cog, inter, **kw):
    try:
        await cmd.callback(cog, inter, **kw)
    except Exception as e:  # mirror discord.py: route to the cog error handler
        from discord import app_commands
        err = app_commands.CommandInvokeError(cmd, e)
        await cog.cog_app_command_error(inter, err)
    return inter


def I(w, user, ch=None, data=None):
    return FakeInteraction(user, ch or w.ch, w.guild, data)


def by_char(g, ch):
    return next(p for p in g.players.values() if g.character(p) == ch)


async def test_full_game_flow(world):
    w, cog = world, world.cog
    # --- signups with 7 players (Adriana's slot needs 8, so it's left out)
    await run(cog.game_new, cog, I(w, w.host), theme="sop", players=7)
    g = cog.game_in_channel(w.ch.id)
    assert g and g.max_players == 7
    for m in w.players[:7]:
        inter = I(w, m, data={"custom_id": f"mafia:join:{g.id}"})
        await cog.on_interaction(inter)
    assert len(g.signups) == 7
    full = I(w, w.players[7], data={"custom_id": f"mafia:join:{g.id}"})
    await cog.on_interaction(full)
    assert "full" in full.said()
    await cog.on_interaction(I(w, w.sub, data={"custom_id": f"mafia:sub:{g.id}"}))
    assert g.subs == [99]

    # non-host can't start
    nope = await run(cog.game_start, cog, I(w, w.players[0]))
    assert "Only the host" in nope.said()

    random.seed(3)
    await run(cog.game_start, cog, I(w, w.host))
    assert g.phase == DAY and len(g.players) == 7
    assert "Adriana" not in {g.character(p) for p in g.players.values()}
    assert all(w.guild.members[p.user_id].dms for p in g.players.values())   # role PMs
    assert "host" in [c.name for c in w.guild.channels.values()]
    assert "mafia-chat" in [c.name for c in w.guild.channels.values()]
    alive_role = w.guild.get_role(g.roles_ids["alive"])
    assert w.ch.perms[alive_role.id] == {"send_messages": True}

    # /role is private
    r = await run(cog.role_cmd, cog, I(w, w.players[0]))
    assert r.out[0][1] is True and r.out[0][0].startswith("You are")

    # --- claims: character only, then a counterclaim
    tony, silvio = by_char(g, "Tony"), by_char(g, "Silvio")
    melfi, harris = by_char(g, "Melfi"), by_char(g, "Harris")
    artie, carm, furio = by_char(g, "Artie"), by_char(g, "Carmela"), by_char(g, "Furio")
    M = lambda p: w.guild.members[p.user_id]  # noqa: E731
    c1 = await run(cog.claim_cmd, cog, I(w, M(melfi)), character="Melfi")
    assert "Melfi → Doctor" in c1.said()
    c2 = await run(cog.claim_cmd, cog, I(w, M(tony)), role="Doctor")
    assert "⚔️" in c2.said() and "Melfi" in c2.said()
    board = await run(cog.claims_cmd, cog, I(w, M(artie)))
    assert "Counterclaims" in board.said()

    # --- text votes + slash votes to a majority lynch (4 of 7)
    for voter in (melfi, harris, artie):
        msg = NS(author=NS(id=voter.user_id, bot=False), guild=w.guild, channel=w.ch,
                 content=f"**VOTE: {tony.name}**", mentions=[], reactions=[])

        async def add_reaction(e, msg=msg):
            msg.reactions.append(e)

        async def mreply(text, mention_author=False, msg=msg):
            msg.reactions.append(text)
        msg.add_reaction, msg.reply = add_reaction, mreply
        await cog.on_message(msg)
        assert msg.reactions == ["✅"], msg.reactions
    vc = await run(cog.votecount_cmd, cog, I(w, M(carm)))
    assert "[3/4]" in vc.said()
    v = await run(cog.vote_cmd, cog, I(w, M(carm)), target=str(tony.user_id))
    assert g.phase == NIGHT and g.locked and not tony.alive               # night starts automatically
    assert w.ch.perms[alive_role.id] == {"send_messages": False}         # channel locked
    assert "has been lynched" in w.ch.text() and "Godfather" in w.ch.text()
    late = await run(cog.vote_cmd, cog, I(w, M(artie)), target=str(silvio.user_id))
    assert "locked" in late.said() or "only open" in late.said()
    dead_role = w.guild.get_role(g.roles_ids["dead"])
    assert dead_role in M(tony).roles and alive_role not in M(tony).roles

    # --- night: mafia kill on Artie, doctor saves him, cop checks Silvio
    await run(cog.host_night, cog, I(w, w.host))
    assert g.phase == NIGHT
    mafia_ch = next(c for c in w.guild.channels.values() if c.name == "mafia-chat")
    fk = await run(cog.factionkill_cmd, cog, I(w, M(silvio), mafia_ch), target=str(artie.user_id))
    assert "will kill" in fk.said()
    await run(cog.action_cmd, cog, I(w, M(melfi)), target=str(artie.user_id))
    await run(cog.action_cmd, cog, I(w, M(harris)), target=str(silvio.user_id))
    bad = await run(cog.action_cmd, cog, I(w, M(carm)), target=str(artie.user_id))
    assert "no night action" in bad.said()
    prev = await run(cog.host_resolve, cog, I(w, w.host))
    assert "protected" in prev.said() and prev.out[0][1] is True
    # dm closed for the cop -> host gets the result to pass on
    M(harris).dm_open = False
    await cog.on_interaction(I(w, w.host, data={"custom_id": f"mafia:apply:{g.id}"}))
    assert g.phase == DAY and g.day == 2 and artie.alive
    host_ch = next(c for c in w.guild.channels.values() if c.name == "host")
    assert "guilty" in host_ch.text()
    assert "Nobody died" in w.ch.text()
    assert w.ch.perms[alive_role.id] == {"send_messages": True}          # unlocked for day 2

    # --- quitting + replacement mid-game keeps votes and claim
    await run(cog.vote_cmd, cog, I(w, M(melfi)), target=str(silvio.user_id))
    q = await run(cog.quit_cmd, cog, I(w, M(melfi)))
    assert melfi.needs_replacement and "Subby" not in q.said()
    assert "replacement is needed" in w.ch.text()
    old_member = M(melfi)
    await run(cog.host_replace, cog, I(w, w.host), player=str(melfi.user_id), new=w.sub)
    assert g.player(99) and g.character(g.player(99)) == "Melfi"
    assert g.votes["99"] == silvio.user_id and "99" in g.claims
    assert alive_role in w.sub.roles and alive_role not in old_member.roles
    assert w.sub.dms and "replaced" in w.ch.text()

    # --- someone leaves the server: flagged for replacement, host told
    await cog.on_member_remove(M(carm))
    assert g.player(carm.user_id).needs_replacement
    # host modkills them instead
    await run(cog.host_modkill, cog, I(w, w.host), player=str(carm.user_id), reason="left")
    assert not g.player(carm.user_id).alive and "modkilled" in w.ch.text()

    # --- finish: lynch the last mafia -> town wins automatically
    alive = g.living()
    need = g.majority()
    voters = [p for p in alive if p.user_id != silvio.user_id][:need]
    for p in voters:
        await run(cog.vote_cmd, cog, I(w, w.guild.members[p.user_id]), target=str(silvio.user_id))
    assert g.phase == ENDED and g.winners == ["Town"]
    assert "Game over" in w.ch.text()
    assert g.id not in cog.games                                        # archived
    saved = await w.store.guild_games(w.guild.id)
    assert saved[0]["phase"] == "ended"

    # cleanup deletes created channels & roles
    await run(cog.game_cleanup, cog, I(w, w.host))
    assert host_ch.deleted and mafia_ch.deleted and not w.guild.roles_


async def test_restart_reloads_running_game(world):
    w, cog = world, world.cog
    await run(cog.game_new, cog, I(w, w.host), theme="sop", players=7)
    g = cog.game_in_channel(w.ch.id)
    for m in w.players[:7]:
        await cog.on_interaction(I(w, m, data={"custom_id": f"mafia:join:{g.id}"}))
    await run(cog.game_start, cog, I(w, w.host))
    p = g.living()[0]
    await run(cog.vote_cmd, cog, I(w, w.guild.members[g.living()[1].user_id]), target=str(p.user_id))
    cog2 = GameCog(w.bot)
    await cog2.cog_load()
    g2 = cog2.game_in_channel(w.ch.id)
    assert g2 and g2.phase == DAY and g2.votes == g.votes


async def test_signup_count_change_and_errors(world):
    w, cog = world, world.cog
    await run(cog.game_new, cog, I(w, w.host), theme="sop")
    g = cog.game_in_channel(w.ch.id)
    assert g.max_players == 8
    r = await run(cog.game_players, cog, I(w, w.host), count=5)
    assert "supports" in r.said()
    await run(cog.game_players, cog, I(w, w.host), count=7)
    assert g.max_players == 7
    dup = await run(cog.game_new, cog, I(w, w.host), theme="sop")
    assert "already a game" in dup.said()
    for m in w.players[:6]:
        await cog.on_interaction(I(w, m, data={"custom_id": f"mafia:join:{g.id}"}))
    st = await run(cog.game_start, cog, I(w, w.host))
    assert "can't seat 6" in st.said()
    assert g.phase == "signups"


async def test_host_tools_and_night_start(world):
    w, cog = world, world.cog
    t = themes.validate({**THEME, "id": "n0", "name": "Night start",
                         "settings": {"start_phase": "night", "reveal_on_death": "alignment",
                                      "auto_night": False}})
    await w.store.save_theme(w.guild.id, t)
    await run(cog.game_new, cog, I(w, w.host), theme="n0", players=7)
    g = cog.game_in_channel(w.ch.id)
    for m in w.players[:7]:
        await cog.on_interaction(I(w, m, data={"custom_id": f"mafia:join:{g.id}"}))
    await run(cog.game_start, cog, I(w, w.host))
    assert g.phase == NIGHT and g.day == 0 and "Night 0" in w.ch.text()
    M = lambda p: w.guild.members[p.user_id]  # noqa: E731
    artie, tony = by_char(g, "Artie"), by_char(g, "Tony")
    # queued host kill lands at dawn even if protected
    melfi = by_char(g, "Melfi")
    await run(cog.action_cmd, cog, I(w, M(melfi)), target=str(artie.user_id))
    q = await run(cog.host_kill, cog, I(w, w.host), player=str(artie.user_id), at_dawn=True)
    assert "Queued" in q.said() and artie.alive
    await run(cog.host_day, cog, I(w, w.host))
    assert not artie.alive and g.phase == DAY and g.day == 1
    assert "Town" in w.ch.text() and "Vanilla Townie" not in w.ch.text()   # alignment-only reveal
    # revive, then host-forced lynch
    await run(cog.host_revive, cog, I(w, w.host), player=str(artie.user_id))
    assert artie.alive
    await run(cog.host_lynch, cog, I(w, w.host), player=str(artie.user_id))
    assert g.phase == DUSK and not artie.alive
    # unlock a day ended by mistake, then end it with plurality (tie -> no lynch)
    await run(cog.host_unlock, cog, I(w, w.host))
    assert g.phase == DAY and not g.locked
    await run(cog.host_endday, cog, I(w, w.host))
    assert g.phase == DUSK and "No one was lynched" in w.ch.text()
    # unclaim / sub leave / transfer / cancel
    await run(cog.claim_cmd, cog, I(w, M(tony)), character="Harris", role="Cop")
    await run(cog.unclaim_cmd, cog, I(w, M(tony)))
    assert str(tony.user_id) not in g.claims
    await run(cog.sub_join, cog, I(w, w.sub))
    await run(cog.sub_leave, cog, I(w, w.sub))
    assert g.subs == []
    living_t = await run(cog.host_transfer, cog, I(w, w.host), new_host=M(tony))
    assert "living player" in living_t.said()
    await run(cog.host_transfer, cog, I(w, w.host), new_host=w.sub)
    assert g.host_id == w.sub.id
    old_host = await run(cog.game_cancel, cog, I(w, w.players[0]))
    assert "Only the host" in old_host.said()
    await run(cog.game_cancel, cog, I(w, w.sub))
    assert g.phase == ENDED and "Cancelled" in w.ch.text()


# ---------------------------------------------------------------- deadlines
import time  # noqa: E402


async def started_game(w, settings=None, n=7):
    t = themes.validate({**THEME, "id": "dl", "name": "Timed", "settings": settings or {}})
    await w.store.save_theme(w.guild.id, t)
    await run(w.cog.game_new, w.cog, I(w, w.host), theme="dl", players=n)
    g = w.cog.game_in_channel(w.ch.id)
    for m in w.players[:n]:
        await w.cog.on_interaction(I(w, m, data={"custom_id": f"mafia:join:{g.id}"}))
    await run(w.cog.game_start, w.cog, I(w, w.host))
    return g


async def test_day_deadline_reminders_and_auto_end(world):
    w, cog = world, world.cog
    g = await started_game(w, {"day_hours": 48, "night_hours": 24, "reminder_hours": "24,1"})
    now = time.time()
    assert g.phase == DAY and abs(g.deadline - (now + 48 * 3600)) < 5
    assert "Day 1 ends <t:" in w.ch.text()
    M = lambda p: w.guild.members[p.user_id]  # noqa: E731
    tony, artie = by_char(g, "Tony"), by_char(g, "Artie")
    await run(cog.vote_cmd, cog, I(w, M(artie)), target=str(tony.user_id))
    await run(cog.vote_cmd, cog, I(w, M(by_char(g, "Melfi"))), target=str(tony.user_id))
    # nothing happens early
    await cog.tick(now + 3600)
    assert g.reminders_sent == [] and g.phase == DAY
    # 24h-left reminder fires once
    await cog.tick(g.deadline - 23.9 * 3600)
    await cog.tick(g.deadline - 23.8 * 3600)
    assert w.ch.text().count("Day 1 ends <t:") == 2        # start post + one reminder
    # 1h-left reminder
    await cog.tick(g.deadline - 1800)
    assert g.reminders_sent == [24.0, 1.0]
    # deadline: plurality (Tony, 2 votes) is lynched, channel locks, night starts with its own deadline
    alive_role = w.guild.get_role(g.roles_ids["alive"])
    day_end = g.deadline
    await cog.tick(day_end + 1)
    assert not tony.alive and "deadline passed" in w.ch.text()
    assert w.ch.perms[alive_role.id] == {"send_messages": False}
    assert g.phase == NIGHT and g.deadline and abs(g.deadline - (time.time() + 24 * 3600)) < 5
    assert "Night 1 actions are due" in w.ch.text()


async def test_night_deadline_locks_actions_then_host_turns_it_around(world):
    w, cog = world, world.cog
    g = await started_game(w, {"start_phase": "night", "night_hours": 12, "day_hours": 48})
    M = lambda p: w.guild.members[p.user_id]  # noqa: E731
    melfi, harris, artie = by_char(g, "Melfi"), by_char(g, "Harris"), by_char(g, "Artie")
    silvio = by_char(g, "Silvio")
    await run(cog.action_cmd, cog, I(w, M(melfi)), target=str(artie.user_id))
    host_ch = next(c for c in w.guild.channels.values() if c.name == "host")
    await cog.tick(g.deadline - 1800)                     # 1h reminder lists who's missing
    assert "Still missing" in host_ch.text() and "Harris" in host_ch.text()
    await cog.tick(g.deadline + 5)
    assert g.actions_closed and g.phase == NIGHT          # host hasn't turned it around yet
    assert "actions are closed" in w.ch.text()
    assert "Night 0 actions are closed (deadline passed)" in host_ch.text()
    late = await run(cog.action_cmd, cog, I(w, M(harris)), target=str(silvio.user_id))
    assert "closed" in late.said()
    late_fk = await run(cog.factionkill_cmd, cog, I(w, M(silvio)), target=str(artie.user_id))
    assert "closed" in late_fk.said()
    # ticking again does nothing new
    n = len(host_ch.sent)
    await cog.tick(g.deadline + 3600)
    assert len(host_ch.sent) == n and g.phase == NIGHT
    # host reopens for a late action, then closes, then applies with a specific day end
    await run(cog.host_reopen, cog, I(w, w.host), until="1h")
    assert not g.actions_closed
    await run(cog.action_cmd, cog, I(w, M(harris)), target=str(silvio.user_id))
    await run(cog.host_closeactions, cog, I(w, w.host))
    assert g.actions_closed
    r = await run(cog.host_day, cog, I(w, w.host), ends="2099-01-01 20:00")
    assert g.phase == DAY and g.day == 1 and not g.actions_closed
    from modfather.timeparse import parse_when
    assert g.deadline == parse_when("2099-01-01 20:00", time.time(), "UTC")
    assert "Day 1 ends" in w.ch.text()


async def test_host_deadline_commands_and_all_in(world):
    w, cog = world, world.cog
    await w.store.set_config(w.guild.id, {"timezone": "America/Chicago"})
    g = await started_game(w, {"day_hours": 0})
    assert g.deadline is None                              # no default: host calls time
    bad = await run(cog.host_deadline, cog, I(w, w.host), when="whenever")
    assert "couldn't read" in bad.said()
    await run(cog.host_deadline, cog, I(w, w.host), when="2099-11-24 8pm")
    from datetime import datetime
    from zoneinfo import ZoneInfo
    assert datetime.fromtimestamp(g.deadline, ZoneInfo("America/Chicago")).hour == 20
    first = g.deadline
    await run(cog.host_extend, cog, I(w, w.host), by="2h")
    assert g.deadline == first + 7200 and "Extended by 2h" in w.ch.text()
    await run(cog.host_deadline, cog, I(w, w.host), when="none")
    assert g.deadline is None
    nothing = await run(cog.host_extend, cog, I(w, w.host), by="2h")
    assert "no deadline" in nothing.said()
    # non-host can't move deadlines
    np_ = await run(cog.host_deadline, cog, I(w, w.players[0]), when="1h")
    assert "Only the host" in np_.said()
    # night: everyone sends actions -> host is told it's all in
    await run(cog.host_night, cog, I(w, w.host))
    assert g.phase == NIGHT
    M = lambda p: w.guild.members[p.user_id]  # noqa: E731
    alive = {g.character(p): p for p in g.living()}
    host_ch = next(c for c in w.guild.channels.values() if c.name == "host")
    await run(cog.action_cmd, cog, I(w, M(alive["Melfi"])), target=str(alive["Artie"].user_id))
    await run(cog.action_cmd, cog, I(w, M(alive["Harris"])), target=str(alive["Silvio"].user_id))
    await run(cog.action_cmd, cog, I(w, M(alive["Furio"])), target=str(alive["Silvio"].user_id))
    assert "Every Night" not in host_ch.text()
    killer = alive.get("Silvio") or alive.get("Tony")
    await run(cog.factionkill_cmd, cog, I(w, M(killer)), target=str(alive["Artie"].user_id))
    assert "Every Night 1 action is in" in host_ch.text()


async def test_deadline_survives_restart_and_old_saves(world):
    w, cog = world, world.cog
    g = await started_game(w, {"day_hours": 24})
    cog2 = GameCog(w.bot)
    await cog2.cog_load()
    g2 = cog2.game_in_channel(w.ch.id)
    assert g2.deadline == g.deadline
    # a game saved before deadlines existed still loads and ticks
    d = g.to_dict()
    for k in ("deadline", "actions_closed", "reminders_sent"):
        d.pop(k)
    for k in ("day_hours", "night_hours", "end_clock", "auto_night", "reminder_hours"):
        d["theme"]["settings"].pop(k)
    from modfather.engine import Game
    old = Game.from_dict(d)
    cog2.games = {old.id: old}
    await cog2.tick()
    assert old.deadline is None and old.phase == DAY


async def test_vote_lookup_and_stats_commands(world):
    w, cog = world, world.cog
    g = await started_game(w, {"day_hours": 0})
    M = lambda p: w.guild.members[p.user_id]  # noqa: E731
    tony, artie, melfi = by_char(g, "Tony"), by_char(g, "Artie"), by_char(g, "Melfi")
    harris, carm = by_char(g, "Harris"), by_char(g, "Carmela")
    for p in (artie, melfi, harris):
        await run(cog.vote_cmd, cog, I(w, M(p)), target=str(tony.user_id))
    await run(cog.vote_cmd, cog, I(w, M(carm)), target=str(artie.user_id))
    await run(cog.vote_cmd, cog, I(w, M(carm)), target=str(tony.user_id))     # majority -> Tony lynched
    assert not tony.alive and g.phase == NIGHT
    # at night, /votecount shows the finished day; day=1 + history lists every vote in order
    vc = await run(cog.votecount_cmd, cog, I(w, M(artie)))
    assert "Final vote count — Day 1" in vc.said() and "majority reached" in vc.said()
    h = await run(cog.votecount_cmd, cog, I(w, M(artie)), day=1, history=True)
    assert "Vote history — Day 1" in h.said() and h.said().count("→") == 5
    bad = await run(cog.votecount_cmd, cog, I(w, M(artie)), day=9)
    assert "Pick a day from 1 to 1" in bad.said()
    # player view: Tony has flipped Mafia, so those votes count publicly
    pv = await run(cog.votestats_cmd, cog, I(w, M(artie)))
    assert pv.out[0][1] is False and "Only targets who have flipped" in pv.said()
    assert "100%" in pv.said()
    # host view is private
    hv = await run(cog.votestats_cmd, cog, I(w, w.host))
    assert hv.out[0][1] is True and "Host view" in hv.said()
    # host can choose the public view
    hp = await run(cog.votestats_cmd, cog, I(w, w.host), private=False)
    assert hp.out[0][1] is False and "Host view" not in hp.said()
