# 🎭 The Modfather

A Discord GM assistant for themed, forum-style Mafia games, plus a web dashboard for building the themes.

- **Themes.** Each faction's roster is a list of character + role pairs ("Dr. Melfi — Doctor"). Roles come from the full Mafia Universe role database (all 475) or are custom roles you write. You can set modifiers (Bulletproof, 1-Shot, Strongman, Ninja…), vote weight and the role PM per character.
- **Role PMs.** Every role has a starter role PM. One click drops a full draft into a character's PM, with placeholders for the character, role and faction and a spot for the character blurb. The GM edits it, and that edited text is what the player gets by DM.
- **Flexible player counts.** Any slot can be "only with N+ players", so one theme covers several player counts. The dashboard shows which counts a theme supports.
- **Deadlines.** Each day has a fixed end date and time. When it passes, the most-voted player is lynched, the channel locks and the night starts. Night actions are due by a deadline. After it, `/action` closes and the host turns the night around whenever they're ready. Reminders go out before each deadline.
- **Claims.** Players claim a character, a role, or both. The bot fills in what a claim implies from the theme (claiming "Dr. Melfi" shows "→ Doctor"). It flags counterclaims, claims of dead characters and pairs that don't exist, and keeps a claim history.
- **Votes.** Majority lynch, with vote weights, Hated/Loved thresholds, No Lynch, and `VOTE: name` text votes. **When a lynch is reached the game channel locks immediately.**
- **Nights.** Host-assisted. Players send actions privately. The bot resolves the common abilities itself and lists the rest for the host. Nothing is applied until the host presses Apply.
- **When things go wrong.** There's a substitute list. `/host replace` hands a slot to a new player along with its votes, claim, night action and role. You also get `/quit`, automatic replacement requests when a player leaves the server, modkills, revives and host transfer. If a player's DMs are closed, their results go to the host channel, and `/role` always works. Games are saved in SQLite and resume after a restart.

---

## Commands

**Players**

| Command | What it does |
|---|---|
| `/role` | See your role PM (only you can see it) |
| `/vote target` · `/unvote` · `/votecount` | Voting. You can also type `VOTE: name` / `UNVOTE` in the game channel |
| `/claim character role note` | Claim a character, a role, or both (all optional, with autocomplete) |
| `/unclaim` · `/claims [history]` | Withdraw a claim · show the claims board, counterclaims and history |
| `/action target [target2] [note]` · `/unaction` | Send or cancel your night action (private), until actions close |
| `/factionkill target` | Send your faction's night kill |
| `/quit` | Ask to leave. The host is told and the sub list is pinged |
| `/sub join` · `/sub leave` · `/sub list` | Substitute list |
| `/game status` · `/game roster` | Who's alive/dead and the current deadline · the setup (open setups only) |

**Hosts**

| Command | What it does |
|---|---|
| `/game new theme [players]` | Open signups in this channel (Join / Leave / Sub list buttons) |
| `/game players count` · `/game add` · `/game remove` | Change the player count during signups; add or remove people |
| `/game start` | Assign roles, DM role PMs, create the host channel, dead chat and faction chats |
| `/host deadline when` | Set when the day ends, or when night actions are due: `Nov 24 8pm`, `fri 9pm`, `2026-11-24 20:00`, `48h`, or `none` |
| `/host extend by` | Push the deadline back: `2h`, `1d`, `30m` |
| `/host closeactions` · `/host reopen [until]` | Close night actions early (e.g. everyone's in) · reopen them for a late action |
| `/host resolve` | Preview the night: deaths, results, what needs your ruling. Button: **Apply & start the day** |
| `/host day [ends]` | Apply the night and start the next day, optionally with a specific end time |
| `/host night` · `/host endday [no_lynch]` · `/host lynch player` | End the day now (plurality) and start the night · end the day · force a lynch |
| `/host actions` | List the night actions sent so far, and who's missing |
| `/host kill player [cause] [at_dawn]` | Kill now, or queue the kill into tonight's deaths (it can't be blocked) |
| `/host modkill` · `/host revive` · `/host replace player new` | Handle inactive players, mistakes and replacements |
| `/host lock` · `/host unlock` | Manual channel lock. If auto-night is off, unlocking a day you ended by mistake reopens voting |
| `/host end winners` · `/host transfer new_host` · `/game cancel` · `/game cleanup` | Finish, hand off, cancel, delete the game's channels and roles |

**Admins**: `/mafia dashboard` (link) · `/mafia config admin_role host_role timezone` (who can edit themes and host games, and which timezone deadline times are read in; Manage Server always counts as admin).

### How a timed game runs

1. **Day N** starts with a deadline: the theme's day length (e.g. 48h), optionally moved to a set clock time like 8pm. The host can set an exact one with `/host day ends:` or `/host deadline`. Discord shows every deadline in each reader's own timezone.
2. Reminders post before the deadline (default: 24h and 1h).
3. If someone hits majority, they're lynched and the channel locks right away. Otherwise, at the deadline, the most-voted player is lynched (ties follow the theme's tie rule).
4. The night starts automatically (this can be turned off) with an action deadline. The host is told when every action is in, and the 1-hour reminder lists who's missing.
5. At the action deadline `/action` closes. The host gets the preview with an **Apply** button, rules on anything marked for them, and applies whenever they're ready. Then the next day starts.

The bot checks deadlines every 20 seconds. If it's offline when a deadline passes, it handles that deadline as soon as it's back.

### What the bot resolves by itself

Kill, strong kill, protect (Doctor), bodyguard, roleblock, jail, alignment check (including Insane/Naive/Paranoid Cop), role check, track, watch, bus drive, redirect and commute. Passives: Godfather (reads innocent), Miller (reads guilty), Bulletproof, Ninja, Macho, Roleblock Immune, Strongman, Lynchproof, Hated, Loved, Doublevoter, Triplevoter, X-shot, odd/even night, non-consecutive, loyal and self-target.

Order each night: commute → jail/roleblock (all at once, so a blocked roleblocker's block still lands) → bus drivers → redirectors → protection → kills → investigations.

All other roles are tagged **host rules** in the dashboard. Players still send actions with `/action`. The night preview lists those actions under "Needs your ruling". You apply the effects with `/host kill` etc. and DM the results yourself.

**Win checks:** Town wins when no Mafia-type players and no killing third parties remain. A Mafia-type faction wins at half the living players or more with no other killers left. A lone killing third party wins at 1-on-1. Anything else (Jester, Survivor, custom wins) is the host's call with `/host end`. Auto-ending can be turned off per theme.

---

## Role PMs

Every one of the 475 roles has starter ability text, written for this bot (it doesn't copy Mafia Universe's own role texts). Each is labeled by how sure the wording is:

| Label | Count | Meaning |
|---|---|---|
| standard | 48 | Well-established meaning across forum Mafia sites |
| typical | 41 | The common version; sites differ |
| check text | 386 | A best reading of the role's name and MU type tags. **Confirm the ability before using it.** |

In the dashboard, open a character's **Role PM** and click **Insert starter PM**, or use **Starter PMs for empty slots** to fill them all at once. A starter looks like:

```
You are **{character}**.
[GM: a line or two about who {character} is in this story.]

You are the **{role}**, aligned with the **{faction}**.
Each night, protect one player from night kills.

You win when every threat to the Town is dead.
```

- `{character}`, `{role}`, `{faction}` and `{player}` are filled in when the PM is sent, so renaming a character later doesn't break the PM.
- Lines containing `[GM: …]` are notes for you and **are never sent to players**. *check text* roles get an extra `[GM: …]` line reminding you to confirm the ability.
- The preview beside the editor shows exactly what the player will see.
- Under the PM, the bot adds the parts that must be accurate: which commands to use at night, the faction kill, teammates and vote weight.
- `/game new` and `/game start` warn the host about any PMs that still have GM notes.

Characters with no PM get a basic card built from the role's starter text.

---

## Setting up

### 1. Discord application

1. Go to https://discord.com/developers/applications → **New Application** → name it **The Modfather**.
2. **Bot** tab → *Reset Token* → copy it (`DISCORD_TOKEN`). Under *Privileged Gateway Intents*, turn on **Server Members Intent** and **Message Content Intent**. Message Content is only needed for `VOTE: name` text votes; if you leave it off, set `TEXT_VOTES=false`.
3. **OAuth2** tab → copy the *Client ID* and *Client Secret*. Add the redirect `https://YOUR-DOMAIN/callback`.
4. Invite the bot (replace CLIENT_ID):
   `https://discord.com/oauth2/authorize?client_id=CLIENT_ID&scope=bot+applications.commands&permissions=268520528`
   (View Channels, Send Messages, Embed Links, Read History, Add Reactions, Manage Channels, Manage Roles.)
5. In Server Settings → Roles, drag the bot's role **above** the roles it should manage. Discord won't let it change roles that sit above it.

### 2. Linode

You need an Ubuntu 22.04/24.04 Linode and a DNS **A record** for the dashboard's subdomain (e.g. `mafia.poor.camp`) pointing at the Linode's IP. Then, on the Linode:

```bash
git clone https://github.com/justinward108/the-modfather.git && cd the-modfather
sudo bash deploy/install.sh mafia.poor.camp
sudo nano /opt/modfather/app/.env      # token, client id/secret, PUBLIC_URL, SESSION_SECRET, DEFAULT_TIMEZONE
sudo systemctl start modfather
journalctl -u modfather -f             # watch the logs
```

`install.sh` installs Python and Caddy (HTTPS certificates are automatic) and creates a `modfather` system user. The bot and dashboard run as one systemd service, and the database lives in `/opt/modfather/data/`. To update later, run `git pull && sudo bash deploy/update.sh`. It backs up the database, then restarts, and running games resume, deadlines included.

Slash commands registered globally can take up to an hour to show up the first time. Set `DEV_GUILD_ID` to your server's id for instant registration while testing.

### 3. Running on your own machine

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env               # PUBLIC_URL=http://localhost:8080, redirect http://localhost:8080/callback
python -m modfather
pytest                             # 68 tests
```

The role catalog is generated from `tools/`: `python tools/make_catalog.py tools/mu_roles.txt modfather/data/roles.json`. To improve a role's starter text, edit `tools/role_texts_*.py` and rerun that command.

---

## Building a theme

1. `/mafia dashboard` → log in with Discord → pick the server → **New theme**.
2. Under each faction, click **+ Add role to …**, search the role list and add roles. Then type a character name for each. "Shown as" renames the role for flavor (e.g. *Godfather* shown as *Boss of Bosses*).
3. Click **Starter PMs for empty slots**, then open each **Role PM**: write the character blurb, confirm any *check text* ability, and delete the `[GM: …]` lines.
4. Set "Only with N+ players" on optional slots. The bar at the bottom shows the player counts the theme supports.
5. Under **Deadlines**, set the day length, the night action deadline, an optional clock time and reminders.
6. Save, then run `/game new` in Discord.

---

## Known limits

- **Not yet run against a live Discord server.** Full games, including deadlines, are tested against fake Discord objects that copy discord.py's interfaces. Real-world differences (role hierarchy, rate limits when creating channels, slow global command sync) will only show up on a real server. Do a test game with a few alts first.
- **Starter role texts:** 386 of 475 are labeled *check text* because they're best readings, not confirmed mechanics. The GM confirms them when writing the PM.
- **Role names** are copied as the MU database page shows them. A few (*Elimination Bait*, *Eliminated*, *Executioner BA*) look like that site's renamed versions of "lynch" roles; I haven't confirmed what they were originally called.
- **Win detection** covers Town, Mafia-type factions and a lone killer. Other wins are called by the host.
- One game per channel. Several games can run at once in different channels.
