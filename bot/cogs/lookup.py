import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.core.embed import Embed
from bot.util import database as db


class Lookup(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.mention_menu = app_commands.ContextMenu(
            name='Mention User',
            callback=self.mention_user_menu,
        )
        self.info_menu = app_commands.ContextMenu(
            name='Get User Information',
            callback=self.user_info_menu,
        )
        self.bot.tree.add_command(self.mention_menu)
        self.bot.tree.add_command(self.info_menu)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None:
            return
        if len(payload.emoji.url.strip()) > 0:
            # Custom emoji bad
            return
        channel_data = await self.bot.get_link_cog().get_channel_data(payload.channel_id)
        if not channel_data:
            return
        emoji = payload.emoji.name
        if emoji != '❓' and emoji != '🔔':
            return
        async with db.MaybeAcquire(pool=self.bot.pool) as con:
            message_data = await con.fetchrow("SELECT * FROM original_messages WHERE message_id = $1;", payload.message_id)
            if not message_data:
                message_data = await con.fetchrow("SELECT * FROM original_messages WHERE message_id = (SELECT original_id FROM synced_messages WHERE message_id = $1);", payload.message_id)
            if not message_data:
                # Doesn't exist anywhere
                return
        guild = self.bot.get_guild(message_data['guild_id'])
        event_guild = self.bot.get_guild(payload.guild_id)
        if emoji == '❓':
            event_author = event_guild.get_member(payload.user_id)
            try:
                await self.send_user_info(event_author, event_guild, message_data, guild)
            except:
                pass
            return
        if guild is None:
            return
        try:
            await self.mention_user(payload.user_id, event_guild, message_data, guild, payload.channel_id)
        except:
            pass

    async def user_info_menu(self, interaction: discord.Interaction, message: discord.Message):
        if interaction.guild_id is None:
            return await interaction.response.send_message("This message is not in a guild!", ephemeral=True)
        channel_data = await self.bot.get_link_cog().get_channel_data(message.channel.id)
        if not channel_data:
            return await interaction.response.send_message("This channel is not linked!", ephemeral=True)
        async with db.MaybeAcquire(pool=self.bot.pool) as con:
            message_data = await con.fetchrow("SELECT * FROM original_messages WHERE message_id = $1;", message.id)
            if not message_data:
                message_data = await con.fetchrow("SELECT * FROM original_messages WHERE message_id = (SELECT original_id FROM synced_messages WHERE message_id = $1);", message.id)
            if not message_data:
                # Doesn't exist anywhere
                return await interaction.response.send_message("I couldn't find information on this message.", ephemeral=True)
        guild = self.bot.get_guild(message_data['guild_id'])
        if not guild:
            await interaction.response.send_message("I don't have access to that guild anymore", ephemeral=True)
        try:
            await self.send_user_info(interaction.user, message.guild, message_data, guild)
        except Exception as e:
            await interaction.response.send_message("DM couldn't be sent! Make sure I can DM you", ephemeral=True)
            return
        await interaction.response.send_message("DM sent!", ephemeral=True)

    async def mention_user_menu(self, interaction: discord.Interaction, message: discord.Message):
        if interaction.guild_id is None:
            return await interaction.response.send_message("This message is not in a guild!", ephemeral=True)
        channel_data = await self.bot.get_link_cog().get_channel_data(message.channel.id)
        if not channel_data:
            return await interaction.response.send_message("This channel is not linked!", ephemeral=True)
        async with db.MaybeAcquire(pool=self.bot.pool) as con:
            message_data = await con.fetchrow("SELECT * FROM original_messages WHERE message_id = $1;", message.id)
            if not message_data:
                message_data = await con.fetchrow("SELECT * FROM original_messages WHERE message_id = (SELECT original_id FROM synced_messages WHERE message_id = $1);", message.id)
            if not message_data:
                # Doesn't exist anywhere
                return await interaction.response.send_message("I couldn't find information on this message.", ephemeral=True)
        guild = self.bot.get_guild(message_data['guild_id'])
        if not guild:
            await interaction.response.send_message("I don't have access to that guild anymore", ephemeral=True)
        try:
            await self.mention_user(interaction.user.id, message.guild, message_data, guild, message.channel.id)
        except:
            await interaction.response.send_message("DM couldn't be sent! Make sure I can DM you", ephemeral=True)
            return
        await interaction.response.send_message("Mentioned", ephemeral=True)

    async def mention_user(self, event_author_id, event_guild, message_data, guild, channel_id):
        channel = guild.get_channel(message_data['channel_id'])
        if channel is None:
            await guild.fetch_channel(message_data['channel_id'])
        embed = Embed()
        embed.set_description("You got mentioned by <@{0}> (`{1}`)".format(event_author_id, event_guild.get_member(event_author_id)))
        await channel.send(f"<@{message_data['author_id']}>", embed=embed)
        if channel_id == message_data['channel_id']:
            return
        event_channel = event_guild.get_channel(channel_id)
        if event_channel is None:
            event_channel = await event_guild.fetch_channel(channel_id)
        await event_channel.send(f"<@{message_data['author_id']}>", embed=embed)

    async def send_user_info(self, event_author, event_guild, message_data, guild):
        dm = event_author.dm_channel
        if dm is None:
            dm = await event_author.create_dm()
        author = event_guild.get_member(message_data['author_id'])
        if not author:
            author = self.bot.get_user(message_data['author_id'])
        if not author:
            await dm.send("User `" + message_data['author_id'] + "` cannot be found in any guilds I am in. They probably have left.")
        else:
            embed = Embed()
            embed.set_author(name=str(author), icon_url=author.display_avatar)
            embed.set_description(f"ID: `{author.id}`")
            await dm.send(embed=embed)
        if guild is not None:
            embed = Embed()
            embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon is not None else None)
            embed.set_description(f"ID: `{guild.id}`\nMembers: `{len(guild.members)}`")
            channel = guild.get_channel(message_data['channel_id'])
            if channel is None:
                await guild.fetch_channel(message_data['channel_id'])
            if channel:
                embed.description += f"\n`#{channel}` (ID: `{channel.id}`) {channel.mention}"
            await dm.send(embed=embed)
        else:
            await dm.send("I don't have access to the guild that sent this message")


async def setup(bot):
    await bot.add_cog(Lookup(bot))
