import math
import typing

import bot as bot_global
import logging
import traceback

import discord
from discord.ext import commands
from datetime import datetime

from bot.core.context import Context
from bot.util.cache import cache

startup_extensions = (
    'bot.cogs.link',
    'bot.cogs.lookup',
    'bot.cogs.management',
)


class Wormhole(commands.Bot):

    def __init__(self, pool, **kwargs):
        self.debug = bot_global.config.get('debug', False)
        self.pool = pool
        allowed_mentions = discord.AllowedMentions(roles=False, everyone=False, users=True)
        intents = discord.Intents(
            guilds=True,
            members=True,
            bans=True,
            emojis=True,
            voice_states=True,
            messages=True,
            reactions=True,
            message_content=True,
            typing=True
        )
        super().__init__(
            command_prefix='&' if not self.debug else '$',
            intents=intents,
            case_insensitive=True,
            owner_id=523605852557672449,
            allowed_mentions=allowed_mentions,
            tags=False,
            **kwargs,
        )
        self.boot = datetime.now()
        self.on_load = []

    def get_link_cog(self):
        return self.get_cog("Link")

    @cache(maxsize=1024)
    async def get_channel_webhook(self, channel: discord.TextChannel):
        webhooks = await channel.webhooks()
        for webhook in webhooks:
            if webhook.name == 'Wormhole Sender':
                return webhook
        return await channel.create_webhook(name='Wormhole Sender')

    async def setup_hook(self) -> None:
        for extension in startup_extensions:
            try:
                await self.load_extension(extension)
            except (discord.ClientException, ModuleNotFoundError):
                logging.warning('Failed to load extension {0}.'.format(extension))
                traceback.print_exc()
        self.loop.create_task(self.run_once_when_ready())

    def run(self):
        super().run(bot_global.config['bot_token'], reconnect=True)

    def add_on_load(self, function):
        self.on_load.append(function)

    async def start(self) -> None:
        await super().start(bot_global.config['bot_token'], reconnect=True)

    async def run_once_when_ready(self):
        await self.wait_until_ready()
        await self.tree.sync()
        print('Ready!')
        for function in self.on_load:
            await function()

    async def on_command_error(self, ctx, error, *, raise_err=True):  # noqa: WPS217
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.CheckFailure):
            return
        if isinstance(error, commands.CommandOnCooldown):
            if await self.is_owner(ctx.author):
                # We don't want the owner to be on cooldown.
                await ctx.reinvoke()
                return
            # Let people know when they can retry
            embed = ctx.create_embed(
                title='Command On Cooldown!',
                description='This command is currently on cooldown. Try again in `{0}` seconds.'.format(math.ceil(error.retry_after)),
                error=True,
            )
            await ctx.delete()
            await ctx.send(embed=embed, delete_after=5)
            return
        if raise_err:
            raise error

    async def get_context(self, origin: typing.Union[discord.Interaction, discord.Message], /, *, cls=Context) -> Context:
        return await super().get_context(origin, cls=cls)

    async def process_commands(self, message):
        if message.author.bot:
            return

        ctx: Context = await self.get_context(message)

        if ctx.command is None:
            return

        try:
            await self.invoke(ctx)
        finally:
            await ctx.release()
