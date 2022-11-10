import re

from discord import utils
from discord.utils import escape_mentions


def clean_content(message, content=None) -> str:
    """:class:`str`: A property that returns the content in a "cleaned up"
    manner. This basically means that mentions are transformed
    into the way the client shows it. e.g. ``<#id>`` will transform
    into ``#name``.

    This will also transform @everyone and @here mentions into
    non-mentions.

    .. note::

        This *does not* affect markdown. If you want to escape
        or remove markdown then use :func:`utils.escape_markdown` or :func:`utils.remove_markdown`
        respectively, along with this function.
    """

    if content is None:
        content = message.content

    if message.guild:

        def resolve_member(id: int) -> str:
            m = message.guild.get_member(id) or utils.get(message.mentions, id=id)  # type: ignore
            return f'`@{m.display_name}`' if m else '`@deleted-user`'

        def resolve_role(id: int) -> str:
            r = message.guild.get_role(id) or utils.get(message.role_mentions, id=id)  # type: ignore
            return f'`@{r.name}`' if r else '`@deleted-role`'

        def resolve_channel(id: int) -> str:
            c = message.guild._resolve_channel(id)  # type: ignore
            return f'`#{c.name}`' if c else '`#deleted-channel`'

    else:

        def resolve_member(id: int) -> str:
            m = utils.get(message.mentions, id=id)
            return f'`@{m.display_name}`' if m else '@deleted-user'

        def resolve_role(id: int) -> str:
            return '`@deleted-role`'

        def resolve_channel(id: int) -> str:
            return f'`#deleted-channel`'

    transforms = {
        '@': resolve_member,
        '@!': resolve_member,
        '#': resolve_channel,
        '@&': resolve_role,
    }

    def repl(match: re.Match) -> str:
        type = match[1]
        id = int(match[2])
        transformed = transforms[type](id)
        return transformed

    result = re.sub(r'<(@[!&]?|#)([0-9]{15,20})>', repl, content)

    return escape_mentions(result)
