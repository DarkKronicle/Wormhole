import logging

import discord
from discord.ext import commands

from bot.core.embed import Embed
from bot.util.webhooker import Webhooker, BasicMessage
from bot.wormhole import Wormhole
from bot.util import database as db, cache


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
              'ALTER TABLE banned ADD CONSTRAINT unique_message UNIQUE(guild_id, user_id);'
        return statement + '\n' + sql


class Channels(db.Table, table_name="channels"):
    link_id = db.Column(db.ForeignKey(table="links", column="id", sql_type=db.Integer(big=True)))
    guild_id = db.Column(db.Integer(big=True))
    channel_id = db.Column(db.Integer(big=True), unique=True)
    invite = db.Column(db.Boolean(), default="false")


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
    async def on_guild_channel_delete(self, channel):
        if not isinstance(channel, discord.TextChannel):
            return
        channel_data = await self.get_channel_data(channel.id)
        if not channel_data:
            return
        async with db.MaybeAcquire(pool=self.bot.pool) as con:
            # Remove from database
            row = await con.fetchrow("DELETE FROM channels WHERE channel_id = $1 RETURNING *;", channel.id)
        self.get_channel_data.invalidate(self, channel.id)
        self.get_link_channels.invalidate(self, row['link_id'])
        self.get_link_data.invalidate(self, row['link_id'])
        self.bot.get_channel_webhook.invalidate(self, channel)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        async with db.MaybeAcquire(pool=self.bot.pool) as con:
            # Remove from database
            rows = await con.fetch("SELECT * FROM links WHERE owner_guild = $1;", guild.id)
            for r in rows:
                channels = await con.fetch("SELECT * FROM channels WHERE link_id = $1;", r['link_id'])
                found = None
                for c in channels:
                    if c['guild_id'] != guild.id:
                        found = c['guild_id']
                        break
                if found:
                    # Migrate to new owner
                    await con.execute("UPDATE links SET owner_guild = $1 WHERE owner_guild = $2;", found, guild.id)
                    await con.execute("DELETE * FROM channels WHERE link_id = $1 AND guild_id = $2;", r['link_id'], guild.id)
                else:
                    await con.execute("DELETE * FROM channels WHERE link_id = $1;", r['link_id'])
                    await con.execute("DELETE * FROM links WHERE id = $1;", r['link_id'])
                self.get_link_channels.invalidate(self, r['link_id'])
                self.get_link_data.invalidate(self, r['link_id'])

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
        self.is_banned.invalidate(self, guild.id, member.id)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, member: discord.User):
        self.is_banned.invalidate(self, guild.id, member.id)

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
        mention_reply = False
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
            if reply.webhook_id is not None:
                mention_reply = message.content.startswith('@')

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
                if mention_reply and original['channel_id'] == channel_id:
                    mention = ' <@{0}>'.format(original['author_id'])
                    self.bot.loop.create_task(self.send_message_and_db(webhooker, message, embed, append=mention))
                else:
                    self.bot.loop.create_task(self.send_message_and_db(webhooker, message, embed))
            else:
                self.bot.loop.create_task(self.send_message_and_db(webhooker, message, None))

    async def send_message_and_db(self, webhooker: Webhooker, message: discord.Message, reply_embed, append=None):
        try:
            response: discord.WebhookMessage = await webhooker.send_message(BasicMessage.from_message(message), wait=True, embed=reply_embed, append=append)
        except:
            response: discord.WebhookMessage = await webhooker.send_message(BasicMessage.from_message(message), wait=True, embed=reply_embed, no_attachments=True, append=append)
        async with db.MaybeAcquire(pool=self.bot.pool) as con:
            await con.execute(
                "INSERT INTO synced_messages(original_id, guild_id, channel_id, message_id) VALUES ($1, $2, $3, $4)",
                message.id,
                response.guild.id,
                response.channel.id,
                response.id,
            )


async def setup(bot):
    await bot.add_cog(Link(bot))
