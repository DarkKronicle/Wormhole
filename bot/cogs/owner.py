from discord.ext import commands

from bot.core.context import Context
from bot.wormhole import Wormhole


class Owner(commands.Cog):

    def __init__(self, bot):
        self.bot: Wormhole = bot

    async def cog_check(self, ctx: Context) -> bool:
        return await self.bot.is_owner(ctx.author)

    @commands.is_owner()
    @commands.command('sync')
    async def sync_commands(self, ctx):
        await self.bot.tree.sync()
        await ctx.send('Done!')


async def setup(bot):
    await bot.add_cog(Owner(bot))
