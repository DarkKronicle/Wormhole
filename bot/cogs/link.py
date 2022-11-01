import logging

import discord
from discord.ext import commands

from bot.core.context import Context
from bot.core.embed import Embed
from bot.util.webhooker import Webhooker, BasicMessage
from bot.wormhole import Wormhole
from bot.util import database as db, cache
import secrets


class Links(db.Table, table_name="links"):
    id = db.Column(db.Integer(), unique=True)
    owner_guild = db.Column(db.Integer(big=True))

    @classmethod
    def create_table(cls, overwrite=False):
        return """CREATE SEQUENCE IF NOT EXISTS link_id_seq;
        CREATE TABLE IF NOT EXISTS links (
            id bigint primary key default pseudo_encrypt(nextval('link_id_seq')),
            owner_guild bigint
        );
        """


class Banned(db.Table, table_name="banned"):
    guild_id = db.Column(db.Integer(big=True))
    user_id = db.Column(db.Integer(big=True))

    @classmethod
    def create_table(cls, overwrite=False):
        statement = super().create_table(overwrite)
        sql = 'ALTER TABLE banned DROP CONSTRAINT IF EXISTS unique_message;' \
              'ALTER TABLE messages ADD CONSTRAINT unique_message UNIQUE(guild_id, user_id);'
        return statement + '\n' + sql


class Channels(db.Table, table_name="channels"):
    link_id = db.Column(db.ForeignKey(table="links", column="id", sql_type=db.Integer(big=True)))
    guild_id = db.Column(db.Integer(big=True))
    channel_id = db.Column(db.Integer(big=True), unique=True)


class OriginalMessages(db.Table, table_name="original_messages"):
    guild_id = db.Column(db.Integer(big=True))
    channel_id = db.Column(db.Integer(big=True))
    author_id = db.Column(db.Integer(big=True))
    message_id = db.Column(db.Integer(big=True), unique=True)


class SyncedMessages(db.Table, table_name="synced_messages"):
    original_id = db.Column(db.ForeignKey(table="original_messages", column="message_id", sql_type=db.Integer(big=True)))
    guild_id = db.Column(db.Integer(big=True))
    channel_id = db.Column(db.Integer(big=True))
    message_id = db.Column(db.Integer(big=True), unique=True)


class Link(commands.Cog):

    def __init__(self, bot):
        self.bot: Wormhole = bot
        self.invites = cache.ExpiringDict(seconds=60 * 15)

    @cache.cache(maxsize=512)
    async def get_link_channels(self, link_id) -> list[dict]:
        async with db.MaybeAcquire(pool=self.bot.pool) as con:
            return await con.fetch('SELECT * FROM channels WHERE link_id = $1;', link_id)

    @cache.cache(maxsize=512)
    async def get_link_data(self, link_id):
        async with db.MaybeAcquire(pool=self.bot.pool) as con:
            return await con.fetchrow('SELECT * FROM links WHERE id = $1;', link_id)

    @cache.cache(maxsize=1024)
    async def get_channel_data(self, channel_id):
        async with db.MaybeAcquire(pool=self.bot.pool) as con:
            return await con.fetchrow('SELECT * FROM channels WHERE channel_id = $1;', channel_id)

    @commands.Cog.listener()
    async def on_typing(self, typing_channel: discord.TextChannel, member: discord.Member, when):
        if typing_channel.guild is None:
            return
        if member.bot:
            # Shouldn't handle this
            return
        channel_data = await self.get_channel_data(typing_channel.id)
        if not channel_data:
            return
        link_data = await self.get_link_channels(channel_data['link_id'])
        if not link_data:
            return
        for channel_row in link_data:
            channel_id = channel_row['channel_id']
            if channel_id == typing_channel.id:
                continue
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                logging.warning("Channel ID " + channel_id + " cannot be found.")
                continue
            await channel.typing()

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, member: discord.User):
        self.is_banned.invalidate(guild.id, member.id)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, member: discord.User):
        self.is_banned.invalidate(guild.id, member.id)

    @cache.cache(maxsize=1024)
    async def is_banned(self, guild_id, user_id):
        guild = self.bot.get_guild(guild_id)
        try:
            ban = await guild.fetch_ban(discord.Object(user_id))
            if ban is not None:
                return True
        except:
            return False
        async with db.MaybeAcquire(pool=self.bot.pool) as con:
            row = await con.fetchrow("SELECT * FROM banned WHERE guild_id = $1 AND user_id = $2;", guild_id, user_id)
        return bool(row)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        if payload.guild_id is None:
            return
        if 'content' not in payload.data:
            # Not being edited with content
            return
        channel_data = await self.get_channel_data(payload.channel_id)
        if channel_data is None:
            return
        async with db.MaybeAcquire(pool=self.bot.pool) as con:
            message_data = await con.fetchrow("SELECT * FROM original_messages WHERE message_id = $1;", payload.message_id)
            if not message_data:
                # Seems to be a proxied message or just doesn't exist
                return
            messages = await con.fetch("SELECT * FROM synced_messages WHERE original_id = $1;", payload.message_id)
        for m in messages:
            channel_id = m['channel_id']
            channel = self.bot.get_channel(channel_id)
            if not channel:
                channel = await self.bot.fetch_channel(channel_id)
            if not channel:
                logging.warning("Couldn't find channel " + channel_id)
                continue
            webhooker = Webhooker(self.bot, channel)
            try:
                await webhooker.edit(m['message_id'], content=payload.data['content'])
            except Exception as e:
                logging.warning(e)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        if payload.guild_id is None:
            return
        channel_data = await self.get_channel_data(payload.channel_id)
        if channel_data is None:
            return
        async with db.MaybeAcquire(pool=self.bot.pool) as con:
            original = True
            message_data = await con.fetchrow("SELECT * FROM original_messages WHERE message_id = $1;", payload.message_id)
            if not message_data:
                original = False
                message_data = await con.fetchrow("SELECT * FROM original_messages WHERE message_id = (SELECT original_id FROM synced_messages WHERE message_id = $1);", payload.message_id)
            if not message_data:
                # Doesn't exist anywhere
                return
            all_messages = await con.fetch("SELECT * FROM synced_messages WHERE original_id = $1;", message_data["message_id"])
            if not original:
                all_messages.append(message_data)
            await con.execute(f"""
                DELETE FROM synced_messages WHERE original_id = {message_data['message_id']};
                DELETE FROM original_messages WHERE message_id = {message_data['message_id']};""")
        for message in all_messages:
            guild_id = message["guild_id"]
            channel_id = message["channel_id"]
            message_id = message["message_id"]
            if channel_id == payload.channel_id:
                continue
            try:
                await discord.PartialMessage(channel=self.bot.get_partial_messageable(channel_id, guild_id=guild_id), id=message_id).delete()
            except Exception as e:
                print(str(guild_id))
                print(str(channel_id))
                print(str(message_id))
                logging.warning(e)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None:
            return
        channel_data = await self.get_channel_data(message.channel.id)
        if not channel_data:
            return
        link_data = await self.get_link_channels(channel_data['link_id'])
        if not link_data:
            return
        if message.author.id == self.bot.user.id:
            # It's the bot
            return
        if message.webhook_id is not None and message.webhook_id == (await self.bot.get_channel_webhook(message.channel)).id:
            # It's the webhook
            return
        async with db.MaybeAcquire(pool=self.bot.pool) as con:
            await con.execute(
                "INSERT INTO original_messages(guild_id, channel_id, message_id, author_id) VALUES ($1, $2, $3, $4)",
                message.guild.id,
                message.channel.id,
                message.id,
                message.author.id
            )
        reply = None
        messages = []
        original = None
        if message.reference is not None:
            reply = message.reference.cached_message
            if not reply:
                reply = await discord.PartialMessage(channel=message.channel, id=message.reference.message_id).fetch()
            original_id = message.reference.message_id
            async with db.MaybeAcquire(pool=self.bot.pool) as con:
                if reply.webhook_id is not None:
                    data = await con.fetchrow("SELECT * FROM synced_messages WHERE message_id = $1;", reply.id)
                    if data:
                        original_id = data['original_id']
                messages = await con.fetch(
                    "SELECT * FROM synced_messages WHERE original_id = $1;", original_id
                )
                original = await con.fetchrow(
                    "SELECT * FROM original_messages WHERE message_id = $1;", original_id
                )
        for channel_row in link_data:
            guild_id = channel_row['guild_id']
            if await self.is_banned(guild_id, message.author.id):
                channel = message.author.dm_channel
                if not channel:
                    channel = await message.author.create_dm()
                try:
                    await channel.send(f"You are currently banned in one of the guilds that is linked to the channel {message.channel.mention}. Because of this, you cannot send messages here.")
                except:
                    pass
                return
            channel_id = channel_row['channel_id']
            if channel_id == message.channel.id:
                continue
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                logging.warning("Channel ID " + channel_id + " cannot be found.")
                continue
            webhooker = Webhooker(self.bot, channel)
            # Create tasks so that they are run all at the same time
            if reply is not None:
                embed = Embed()
                embed.set_author(name=reply.author.display_name, icon_url=reply.author.display_avatar.url)
                content = reply.content
                if len(content) > 50:
                    content = content[:50]
                jump_url = None
                for m in messages:
                    if m['channel_id'] == channel_id:
                        jump_url = f'https://discord.com/channels/{m["guild_id"]}/{channel_id}/{m["message_id"]}'
                        break
                if jump_url is None and original is not None:
                    jump_url = f'https://discord.com/channels/{original["guild_id"]}/{original["channel_id"]}/{original["message_id"]}'
                embed.set_description(f"**[Reply To: ]({jump_url}) **{content}")
                self.bot.loop.create_task(self.send_message_and_db(webhooker, message, embed))
            else:
                self.bot.loop.create_task(self.send_message_and_db(webhooker, message, None))

    async def send_message_and_db(self, webhooker: Webhooker, message: discord.Message, reply_embed):
        try:
            response: discord.WebhookMessage = await webhooker.send_message(BasicMessage.from_message(message), wait=True, embed=reply_embed)
        except:
            response: discord.WebhookMessage = await webhooker.send_message(BasicMessage.from_message(message), wait=True, embed=reply_embed, no_attachments=True)
        async with db.MaybeAcquire(pool=self.bot.pool) as con:
            await con.execute(
                "INSERT INTO synced_messages(original_id, guild_id, channel_id, message_id) VALUES ($1, $2, $3, $4)",
                message.id,
                response.guild.id,
                response.channel.id,
                response.id,
            )

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
        channel_data = await self.get_channel_data(channel.id)
        if channel_data is not None:
            return await ctx.send("That channel is already linked!", ephemeral=True)
        if invite_id not in self.invites:
            return await ctx.send("Invalid invite code!", ephemeral=True)
        link_id = self.invites[invite_id]
        data = await self.get_link_data(link_id)
        if data is None:
            return await ctx.send("That link has disappeared!", ephemeral=True)
        async with db.MaybeAcquire(pool=self.bot.pool) as con:
            await con.execute("INSERT INTO channels (link_id, guild_id, channel_id) VALUES ($1, $2, $3);", link_id, channel.guild.id, channel.id)
        self.get_channel_data.invalidate(channel.id)
        self.get_link_channels.invalidate(link_id)
        await ctx.send("Entanglement complete!")

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
        channel_data = await self.get_channel_data(channel.id)
        if channel_data is not None:
            return await ctx.send("That channel is already linked!", ephemeral=True)
        async with db.MaybeAcquire(pool=self.bot.pool) as con:
            result = await con.fetchrow("INSERT INTO links (owner_guild) VALUES ($1) RETURNING *;", ctx.guild.id)
            await con.execute("INSERT INTO channels (link_id, guild_id, channel_id) VALUES ($1, $2, $3);", result['id'], channel.guild.id, channel.id)
        self.get_channel_data.invalidate(channel.id)
        self.get_link_data.invalidate(result['id'])
        self.get_link_channels.invalidate(result['id'])
        await ctx.send("Created link with id `{0}`".format(result['id']))

    @commands.hybrid_command("about")
    async def about(self, ctx: Context):
        """Gets help information
        """
        channel = ctx.author.dm_channel
        if channel is None:
            channel = await ctx.author.create_dm()
        try:
            await channel.send("""
            Hello there! I am a bot to link different channels together through *time and space!* (Well, it's really just discord servers). When a channel is linked (entangled) all messages get synced. It may appear that some people are bots, but that is just a discord limitation.
            
            In a channel that is linked you can do `/info <channel>` to get current information. Within that channel you can react with ❓ to get information about the message. React with 🔔 to ping that user.
            
            __**To setup**__
            If you have `Manage Guild` permissions you can use the command `/createlink <channel>` to create a link. Then you can use `/invitecode <linkid>` to create an invite code other servers can use to link one of their channels to channel the link was created in.
            """.replace("    ", ""))
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
        channel_data = await self.get_channel_data(channel.id)
        if not channel_data:
            return await ctx.send("That channel is not linked!", ephemeral=True)
        link_channels = await self.get_link_channels(channel_data['link_id'])
        link_data = await self.get_link_data(channel_data['link_id'])
        if not link_channels:
            return await ctx.send("That channel is not linked!", ephemeral=True)
        embed = Embed()
        embed.set_title(f'{channel} Link Information')
        guild = self.bot.get_guild(link_data['owner_guild'])
        guilds = []
        for channel in link_channels:
            formatted = '`' + str(self.bot.get_guild(channel['guild_id'])) + '`'
            if formatted not in guilds:
                guilds.append(formatted)
        embed.set_description(f"Owner Guild: `{guild}`\nLink ID: `{channel_data['link_id']}`\nChannels Linked: `{len(link_channels)}`\nGuilds: {', '.join(guilds)}")
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
        data = await self.get_link_data(link)
        if not data:
            return await ctx.send("Invalid link ID!", ephemeral=True)
        if data['owner_guild'] != ctx.guild.id:
            return await ctx.send("This guild does not own the link!", ephemeral=True)
        invite_id = secrets.randbits(28)
        self.invites[invite_id] = link
        await ctx.send(f"Your invite ID is `{invite_id}`. This will expire in 15 minutes. Have an admin use `/link <invite_id> <channel>` in the desired link server and channel.", ephemeral=True)

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
        self.is_banned.invalidate(ctx.guild.id, user.id)
        await ctx.send(f"Successfully banned `{user}` from all links that communicate with this server.")

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
        self.is_banned.invalidate(ctx.guild.id, user.id)
        await ctx.send(f"Successfully unbanned `{user}` from links that communicate with this server.")


async def setup(bot):
    await bot.add_cog(Link(bot))
