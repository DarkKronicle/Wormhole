from __future__ import annotations
import discord
import typing
from functools import wraps
import re
from collections import defaultdict

from typing import Optional, TYPE_CHECKING


def build_dict(messages: list[discord.Message], *, loose=False, depth=-1) -> dict[int, list[discord.Message]]:
    pairs = defaultdict(list)
    last_found = None
    d = depth
    for m in messages:
        if m.reference is not None:
            found = next((s for s in messages if s.id == m.reference.message_id), None)
            if found is not None:
                pairs[found.id].append(m)
                last_found = found.id
                d = depth
        else:
            if d == 0:
                continue
            d = d - 1
            if loose and last_found is not None:
                pairs[last_found].append(m)
                last_found = m.id
    return pairs


def get_referenced_from(reference: int, message_dict: dict[int, list[discord.Message]]) -> typing.Optional[int]:
    for key, value in message_dict.items():
        found = next((s for s in value if s.id == reference), None)
        if found is not None:
            return key
    return None


def get_first_referenced(reference: int, message_dict: dict[int, list[discord.Message]]) -> int:
    found = get_referenced_from(reference, message_dict)
    f = None
    while found is not None:
        f = found
        found = get_referenced_from(found, message_dict)
    return f


def extend_all(reference: int, message_dict: dict[int, list[discord.Message]], arr: list[discord.Message], depth=-1, orig_depth=-1) -> None:
    if depth == 0:
        return
    for r in message_dict[reference]:
        is_in = next((s for s in arr if s.id == r.id), None)
        if is_in:
            continue
        arr.append(r)
        if r.reference is not None:
            depth = orig_depth
        extend_all(r.id, message_dict, arr, depth - 1, orig_depth)


def ensure_webhook(func):
    @wraps(func)
    async def wrapped(self, *args, **kwargs):
        await self.setup_webhook()
        return await func(self, *args, **kwargs)
    return wrapped


def get_name(content):
    content = content.replace('[', '').replace(']', '')
    data = re.split(r'\s', content)
    if len(data) > 5:
        data = data[:5]
    data = ' '.join(data)
    if len(data) > 30:
        data = data[:30]
    return data


class BasicMessage:

    def __init__(
            self,
            author: discord.User,
            attachments: Optional[list[discord.Attachment]],
            embeds: Optional[list[discord.Embed]],
            content: str,
    ):
        self.author = author
        self.attachments = attachments or []
        self.embeds = embeds or []
        self.content = content

    def copy(self):
        return BasicMessage(self.author, self.attachments.copy(), [e.copy() for e in self.embeds], self.content)

    @classmethod
    def from_message(cls, message: discord.Message):
        return cls(message.author, message.attachments, message.embeds, message.content)


class Webhooker:

    def __init__(self, bot, channel: discord.TextChannel):
        self.webhook: discord.Webhook = None
        self.channel = channel
        self.bot = bot

    async def setup_webhook(self):
        if self.webhook is not None:
            return
        self.webhook = await self.bot.get_channel_webhook(self.channel)

    @ensure_webhook
    async def edit(self, message_id, thread=discord.utils.MISSING, **kwargs):
        # Can't modify what user looks like
        kwargs.pop('avatar_url', None)
        kwargs.pop('username', None)
        return await self.webhook.edit_message(message_id, thread=thread, **kwargs)

    @ensure_webhook
    async def send(self, thread=discord.utils.MISSING, **kwargs):
        return await self.webhook.send(thread=thread, **kwargs)

    @ensure_webhook
    async def create_thread(self, name, **kwargs):
        return await self.webhook.send(thread_name=name, **kwargs)

    async def send_message(self, message: BasicMessage, *, no_attachments=False, thread=None, append=None, **kwargs) -> typing.Optional[discord.WebhookMessage]:
        files = []
        if not no_attachments:
            for attachment in message.attachments:
                files.append(await attachment.to_file())
        if thread is None:
            thread = discord.utils.MISSING
        embed = kwargs.pop("embed", None)
        embeds = message.embeds
        if embed:
            embeds = embeds + [embed]
        content = message.content
        if append:
            content = content + append
        return await self.mimic_user(
            member=message.author,
            embeds=embeds,
            content=content,
            allowed_mentions=discord.AllowedMentions(everyone=False, users=True, roles=False),
            thread=thread,
            files=files,
            **kwargs,
        )

    @ensure_webhook
    async def mimic_user(self, member: discord.Member, **kwargs) -> typing.Optional[discord.WebhookMessage]:
        new_kwargs = {}
        for key, value in kwargs.items():
            if value is not None:
                if isinstance(value, str) and len(value) == 0:
                    continue
                new_kwargs[key] = value
        return await self.webhook.send(
            username=member.display_name,
            avatar_url=member.display_avatar.url,
            **new_kwargs,
        )

    @ensure_webhook
    async def send_channel_messages(self, messages: list[discord.Message], *, creator: discord.Member = None, thread: discord.Thread = None, interaction: discord.Interaction = None):
        if creator is None:
            creator = messages[0].author
        embed = discord.Embed(
            description="{0} Pulled {1} messages starting from **[here]({2})**".format(
                creator.mention,
                len(messages),
                messages[0].jump_url),
            timestamp=messages[0].created_at,
        )
        embed.set_author(icon_url=creator.display_avatar.url, name='Requested by {0}'.format(creator.display_name))
        if interaction is not None:
            await interaction.edit_original_response(embed=embed)
        else:
            if thread is None:
                await self.channel.send(embed=embed)
            else:
                await thread.send(embed=embed)
        for mes in self.flatten(messages):
            await self.send_message(mes, thread=thread)

    @ensure_webhook
    async def create_thread_with_messages(self, messages: list[discord.Message], *, creator: discord.Member = None, interaction: discord.Interaction = None):
        if creator is None:
            creator = messages[0].author
        embed = discord.Embed(
            description="{0} Pulled {1} messages starting from **[here]({2})**".format(
                creator.mention,
                len(messages),
                messages[0].jump_url),
            timestamp=messages[0].created_at,
        )
        embed.set_author(icon_url=creator.display_avatar.url, name='Requested by {0}'.format(creator.display_name))
        # Send through channel so we get good-looking message
        if interaction is not None:
            m = await interaction.edit_original_response(embed=embed)
        else:
            m = await self.channel.send(embed=embed)
        name = messages[0].content
        if not name:
            name = 'Blank'

        for mes in self.flatten(messages):
            await self.send_message(mes, thread=thread)

    @staticmethod
    def flatten(messages: list[discord.Message]):
        previous_message: BasicMessage = None
        basic_messages = []
        for mes in messages:
            basic = BasicMessage.from_message(mes)
            if (
                    previous_message is None
                    or
                    basic.author.id != previous_message.author.id
                    or
                    not (len(basic.embeds) == 0 and len(previous_message.embeds) == 0)
                    or
                    not (len(basic.attachments) == 0 and len(previous_message.attachments) == 0)
            ):
                basic_messages.append(basic)
                previous_message = basic
                continue
            new_content = previous_message.content + '\n' + basic.content
            if len(new_content) > 2000:
                basic_messages.append(basic)
                previous_message = basic
                continue
            previous_message.content = new_content
        return basic_messages
