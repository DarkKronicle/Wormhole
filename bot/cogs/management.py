import secrets
from collections import defaultdict

import discord
from discord.ext import commands

from bot.core.context import Context
from bot.core.embed import Embed
from bot.util import database as db
from bot.util.cache import ExpiringDict
from bot.wormhole import Wormhole


class Management(commands.Cog):

    def __init__(self, bot):
        self.bot: Wormhole = bot
        self.invites = ExpiringDict(60 * 15)

    @commands.hybrid_command("link")
    async def link_channel(self, ctx: Context, invite_id: int, channel: discord.TextChannel):
        """Links a channel to an already created link

        :param invite_id: The invite ID
        :param channel: The channel to link
        """
        if ctx.guild is None or channel.guild.id != ctx.guild.id:
            return await ctx.send("You have to be in the guild!", ephemeral=True)
        if not ctx.author.guild_permissions.manage_guild:
            return await ctx.send("You do not have permission to create a link!", ephemeral=True)
        channel_data = await self.bot.get_link_cog().get_channel_data(channel.id)
        if channel_data is not None:
            return await ctx.send("That channel is already linked!", ephemeral=True)
        if invite_id not in self.invites:
            return await ctx.send("Invalid invite code!", ephemeral=True)
        link_id = self.invites[invite_id]
        del self.invites[invite_id]
        data = await self.bot.get_link_cog().get_link_data(link_id)
        if data is None:
            return await ctx.send("That link has disappeared!", ephemeral=True)
        async with db.MaybeAcquire(pool=self.bot.pool) as con:
            await con.execute(
                "INSERT INTO channels (link_id, guild_id, channel_id) VALUES ($1, $2, $3);", link_id, channel.guild.id, channel.id
                )
        self.bot.get_link_cog().get_channel_data.invalidate(self.bot.get_link_cog(), channel.id)
        self.bot.get_link_cog().get_link_channels.invalidate(self.bot.get_link_cog(), link_id)
        self.bot.get_link_cog().get_link_data.invalidate(self.bot.get_link_cog(), link_id)
        self.bot.get_link_cog().bot.get_channel_webhook.invalidate(self.bot.get_link_cog(), channel)
        await ctx.send("Entanglement complete!")
        channels = await self.bot.get_link_cog().get_link_channels(link_id)
        embed = Embed()
        embed.set_title("New entanglement!")
        embed.set_description(f"{channel.guild} is now entangled with this channel!")
        for channel_data in channels:
            guild = self.bot.get_guild(channel_data['guild_id'])
            c = guild.get_channel(channel_data['channel_id'])
            if c is None:
                c = await guild.fetch_channel(channel_data['channel_id'])
            await c.send(embed=embed)

    @commands.hybrid_command("createlink")
    async def link_create(self, ctx: Context, channel: discord.TextChannel):
        """Creates a link tied to a specific channel

        :param channel: The channel to link
        """
        if ctx.guild is None or channel.guild.id != ctx.guild.id:
            return await ctx.send("You have to be in the guild!", ephemeral=True)
        if isinstance(channel, discord.Thread):
            return await ctx.send("It has to be a full text channel!", ephemeral=True)
        if not ctx.author.guild_permissions.manage_guild:
            return await ctx.send("You do not have permission to create a link!", ephemeral=True)
        channel_data = await self.bot.get_link_cog().get_channel_data(channel.id)
        if channel_data is not None:
            return await ctx.send("That channel is already linked!", ephemeral=True)
        async with db.MaybeAcquire(pool=self.bot.pool) as con:
            result = await con.fetchrow("INSERT INTO links (owner_guild) VALUES ($1) RETURNING *;", ctx.guild.id)
            await con.execute(
                "INSERT INTO channels (link_id, guild_id, channel_id) VALUES ($1, $2, $3);", result['id'], channel.guild.id, channel.id
                )
        self.bot.get_link_cog().get_channel_data.invalidate(self.bot.get_link_cog(), channel.id)
        self.bot.get_link_cog().get_link_data.invalidate(self.bot.get_link_cog(), result['id'])
        self.bot.get_link_cog().get_link_channels.invalidate(self.bot.get_link_cog(), result['id'])
        await ctx.send("Created link with id `{0}`".format(result['id']))

    @commands.hybrid_command("about")
    async def about(self, ctx: Context):
        """Gets help information
        """
        channel = ctx.author.dm_channel
        if channel is None:
            channel = await ctx.author.create_dm()
        try:
            await channel.send(
                """
            Hello there! I am a bot to link different channels together through *time and space!* (Well, it's really just discord servers). When a channel is linked (entangled) all messages get synced. It may appear that some people are bots, but that is just a discord limitation.

            In a channel that is linked you can do `/info <channel>` to get current information. Within that channel you can react with ❓ to get information about the message. React with 🔔 to ping that user.

            __**To setup**__
            If you have `Manage Guild` permissions you can use the command `/createlink <channel>` to create a link. Then you can use `/invitecode <linkid>` to create an invite code other servers can use to link one of their channels to channel the link was created in.
            """.replace("    ", "")
                )
            await ctx.send("Check your DMs!", ephemeral=True)
        except:
            await ctx.send("I couldn't DM you help information! Make sure I'm not blocked!", ephemeral=True)

    @commands.hybrid_command("info")
    async def get_info(self, ctx: Context, channel: discord.TextChannel):
        """Gets information about a channel

        :param channel: The channel to inspect
        """
        if ctx.guild is None:
            return await ctx.send("You have to be in a guild!", ephemeral=True)
        channel_data = await self.bot.get_link_cog().get_channel_data(channel.id)
        if not channel_data:
            return await ctx.send("That channel is not linked!", ephemeral=True)
        link_channels = await self.bot.get_link_cog().get_link_channels(channel_data['link_id'])
        link_data = await self.bot.get_link_cog().get_link_data(channel_data['link_id'])
        if not link_channels:
            return await ctx.send("That channel is not linked!", ephemeral=True)
        embed = Embed()
        embed.set_title(f'{channel} Link Information')
        guild = self.bot.get_guild(link_data['owner_guild'])
        channel_guilds = defaultdict(list)
        for channel in link_channels:
            guild = self.bot.get_guild(channel['guild_id'])
            channel_guilds[channel['guild_id']].append(f"{guild.get_channel(channel['channel_id'])} (`{channel['channel_id']}`)")
        formatted = []
        for guild, channels in channel_guilds.items():
            formatted.append("**" + str(self.bot.get_guild(guild)) + "**: " + ', '.join(channels))
        lines = '\n'.join(formatted)
        embed.set_description(
            f"Owner Guild: `{guild}`\nLink ID: `{channel_data['link_id']}`\nChannels Linked: `{len(link_channels)}`\n\n```\nGuilds``` {lines}"
        )
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command("invitecode")
    async def invite_code(self, ctx: Context, link: str):
        """Generates an invite code to a link to use with /link

        :param link: The link ID gathered using /info
        """
        try:
            link = int(link)
        except:
            return await ctx.send("Invalid link ID (has to be an int)", ephemeral=True)
        if not ctx.author.guild_permissions.manage_guild:
            return await ctx.send("You do not have permission to create a link!", ephemeral=True)
        data = await self.bot.get_link_cog().get_link_data(link)
        if not data:
            return await ctx.send("Invalid link ID!", ephemeral=True)
        if data['owner_guild'] != ctx.guild.id:
            return await ctx.send("This guild does not own the link!", ephemeral=True)
        invite_id = secrets.randbits(28)
        self.invites[invite_id] = link
        await ctx.send(
            f"Your invite ID is `{invite_id}`. This will expire in 15 minutes. Have an admin use `/link <invite_id> <channel>` in the desired link server and channel.",
            ephemeral=True
            )

    @commands.hybrid_command("linkban")
    async def link_ban(self, ctx: Context, user: discord.User):
        """Bans a user from using any links tied to this server

        :param user: The user to ban
        """
        if ctx.guild is None:
            return await ctx.send("You have to be in the guild!", ephemeral=True)
        if not ctx.author.guild_permissions.ban_members:
            return await ctx.send("You do not have permission to ban a member from the link!", ephemeral=True)
        async with db.MaybeAcquire(pool=self.bot.pool) as con:
            await con.execute("INSERT INTO banned (guild_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING;", ctx.guild.id, user.id)
        self.bot.get_link_cog().is_banned.invalidate(self.bot.get_link_cog(), ctx.guild.id, user.id)
        await ctx.send(f"Successfully banned `{user}` from all links that communicate with this server.")

    @commands.hybrid_command("unlink")
    async def unlink(self, ctx: Context, channel: discord.TextChannel):
        """Unlinks a channel

        :param channel: The channel to unlink
        """
        if ctx.guild is None or channel.guild.id != ctx.guild.id:
            return await ctx.send("You have to be in the guild!", ephemeral=True)
        if isinstance(channel, discord.Thread):
            return await ctx.send("It has to be a full text channel!", ephemeral=True)
        if not ctx.author.guild_permissions.manage_guild:
            return await ctx.send("You do not have permission to unlink!", ephemeral=True)
        channel_data = await self.bot.get_link_cog().get_channel_data(channel.id)
        if channel_data is None:
            return await ctx.send("That channel is already not linked!", ephemeral=True)
        async with db.MaybeAcquire(pool=self.bot.pool) as con:
            data = await con.fetchrow("DELETE FROM channels WHERE channel_id = $1 AND guild_id = $2 RETURNING *;", channel.id, channel.guild.id)
        await ctx.send("Channel has been untangled!")
        link_id = data['link_id']
        self.bot.get_link_cog().get_channel_data.invalidate(self.bot.get_link_cog(), channel.id)
        self.bot.get_link_cog().get_link_channels.invalidate(self.bot.get_link_cog(), link_id)
        self.bot.get_link_cog().get_link_data.invalidate(self.bot.get_link_cog(), link_id)
        self.bot.get_link_cog().bot.get_channel_webhook.invalidate(self.bot.get_link_cog(), channel)

    @commands.hybrid_command("unlink")
    async def unlink(self, ctx: Context, channel_id: str):
        """Unlinks a channel

        :param channel: The channel ID to unlink
        """
        try:
            channel_id = int(channel_id)
        except:
            return await ctx.send("Invalid ID! (not an int)", ephemeral=True)
        channel = self.bot.get_channel(channel_id)
        if not channel:
            return await ctx.send("Invalid channel! (not an int)", ephemeral=True)
        if ctx.guild is None:
            return await ctx.send("You have to be in a guild!", ephemeral=True)
        if isinstance(channel, discord.Thread):
            return await ctx.send("It has to be a full text channel!", ephemeral=True)
        if not ctx.author.guild_permissions.manage_guild:
            return await ctx.send("You do not have permission to unlink!", ephemeral=True)
        channel_data = await self.bot.get_link_cog().get_channel_data(channel.id)
        if channel_data is None:
            return await ctx.send("That channel is already not linked!", ephemeral=True)
        link_data = await self.bot.get_link_cog().get_link_data(channel_data['link_id'])
        if link_data['owner_guild'] != ctx.guild.id:
            return await ctx.send("You aren't the owner of the link!")
        async with db.MaybeAcquire(pool=self.bot.pool) as con:
            data = await con.fetchrow("DELETE FROM channels WHERE channel_id = $1 AND guild_id = $2 RETURNING *;", channel.id, channel.guild.id)
        await ctx.send("Channel has been untangled!")
        link_id = data['link_id']
        self.bot.get_link_cog().get_channel_data.invalidate(self.bot.get_link_cog(), channel.id)
        self.bot.get_link_cog().get_link_channels.invalidate(self.bot.get_link_cog(), link_id)
        self.bot.get_link_cog().get_link_data.invalidate(self.bot.get_link_cog(), link_id)
        self.bot.get_link_cog().bot.get_channel_webhook.invalidate(self.bot.get_link_cog(), channel)

    @commands.hybrid_command("linkunban")
    async def link_ban(self, ctx: Context, user: discord.User):
        """Unbans a user from using any links tied to this server

        :param user: The user to unban
        """
        if ctx.guild is None:
            return await ctx.send("You have to be in the guild!", ephemeral=True)
        if not ctx.author.guild_permissions.ban_members:
            return await ctx.send("You do not have permission to unban a member from the link!", ephemeral=True)
        async with db.MaybeAcquire(pool=self.bot.pool) as con:
            await con.execute("DELETE FROM banned WHERE guild_id = $1 AND user_id = $2;", ctx.guild.id, user.id)
        self.self.bot.get_link_cog().invalidate(self.bot.get_link_cog(), ctx.guild.id, user.id)
        await ctx.send(f"Successfully unbanned `{user}` from links that communicate with this server.")


async def setup(bot):
    await bot.add_cog(Management(bot))
