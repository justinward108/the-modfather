"""The Modfather entrypoint: runs the Discord bot and the dashboard web server in one process."""
from __future__ import annotations

import asyncio
import logging

import discord
from aiohttp import web
from discord.ext import commands

from . import config
from .cog import GameCog
from .store import Store
from .web import make_app

log = logging.getLogger("modfather")


class BotAccess:
    """What the dashboard needs to know from Discord: which servers a user may manage."""

    def __init__(self, bot: "Modfather"):
        self.bot = bot

    async def _member(self, guild: discord.Guild, user_id: int):
        m = guild.get_member(user_id)
        if m is None:
            try:
                m = await guild.fetch_member(user_id)
            except discord.HTTPException:
                return None
        return m

    async def manageable_guilds(self, user_id: int) -> list[dict]:
        cog: GameCog = self.bot.get_cog("GameCog")
        out = []
        for g in self.bot.guilds:
            m = g.get_member(user_id)
            if m and await cog.is_admin(m):
                out.append({"id": str(g.id), "name": g.name,
                            "icon": g.icon.url if g.icon else None})
        return out

    async def can_manage(self, user_id: int, guild_id: int) -> bool:
        g = self.bot.get_guild(guild_id)
        if not g:
            return False
        m = await self._member(g, user_id)
        cog: GameCog = self.bot.get_cog("GameCog")
        return bool(m and await cog.is_admin(m))


class Modfather(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True                       # replacements when people leave; dashboard access
        intents.message_content = config.TEXT_VOTES  # "VOTE: name" parsing
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.store = Store(config.DB_PATH)
        self.web_runner: web.AppRunner | None = None

    async def setup_hook(self):
        await self.store.open()
        cog = GameCog(self)
        await self.add_cog(cog)
        self.scheduler = asyncio.create_task(cog.run_scheduler())
        if config.DEV_GUILD_ID:
            guild = discord.Object(id=config.DEV_GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Synced commands to dev guild %s", config.DEV_GUILD_ID)
        else:
            await self.tree.sync()
            log.info("Synced commands globally (can take a while to appear)")
        app = make_app(self.store, BotAccess(self))
        self.web_runner = web.AppRunner(app)
        await self.web_runner.setup()
        await web.TCPSite(self.web_runner, config.WEB_HOST, config.WEB_PORT).start()
        log.info("Dashboard on http://%s:%s (public: %s)", config.WEB_HOST, config.WEB_PORT,
                 config.PUBLIC_URL)

    async def on_ready(self):
        log.info("Logged in as %s in %d servers", self.user, len(self.guilds))

    async def close(self):
        if self.web_runner:
            await self.web_runner.cleanup()
        await self.store.close()
        await super().close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not config.DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in.")
    bot = Modfather()
    bot.run(config.DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
