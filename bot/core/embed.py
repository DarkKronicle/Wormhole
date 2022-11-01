import discord
from typing import Optional, Any, Tuple, Callable, Union

from bot.util import formatter
import base64
import json
from discord.utils import MISSING


class Embed(discord.Embed):

    def __init__(
            self,
            *,
            inline=True,
            max_description=4096,
            max_field=1024,
            truncate_append='',
            fields: Union[Tuple[Any, Any], Any] = (),
            description_formatter: Optional[Callable] = None,
            value_formatter: Optional[Callable] = None,
            **kwargs,
    ):
        desc = kwargs.pop('description', '')
        self._description = desc
        self.max_description = max_description
        self.description_formatter = description_formatter
        self.max_field = max_field
        self.inline = inline
        self.truncate_append = truncate_append
        self.field_formatter = value_formatter
        self.active_description = True
        self.active_thumbnail = True
        self.active_image = True
        self.active_author = True
        self.active_title = True
        self.active_url = True
        super().__init__(**kwargs)
        self.set_fields(fields)
        if desc:
            self.set_description(desc)

    def set_fields(self, fields: Union[Tuple[Any, Any], Any], *, value_formatter: Optional[Callable] = None, inline: Optional[bool] = None):
        self.clear_fields()
        self.append_fields(fields, value_formatter=value_formatter, inline=inline)

    def append_fields(self, fields: Tuple[Any, Any], *, value_formatter: Optional[Callable] = None, inline: Optional[bool] = None):
        for f in fields:
            if isinstance(f, tuple):
                self.add_field(name=f[0], value=f[1], value_formatter=value_formatter, inline=inline)
            else:
                self.add_field(name=f.name, value=f.value, value_formatter=value_formatter, inline=inline)

    @property
    def description(self):
        return self._description

    @description.setter
    def description(self, description):
        self.set_description(description)

    def set_description(
            self, description: Union[str, None], *, max_description: Optional[int] = None, truncate_append: Optional[str] = None, description_formatter: Optional[str] = None,
    ):
        if description is None:
            self._description = ''
            # Setting it blank makes it easier to modify it, but on `to_dict` None/empty are the same
            return
        if description_formatter is None:
            description_formatter = self.description_formatter

        if description_formatter is None:
            description_formatter = formatter.blank_formatter

        if max_description is None:
            max_description = self.max_description

        if truncate_append is None:
            truncate_append = self.truncate_append

        if len(description) > max_description:
            description = description[:(max_description - len(truncate_append))]
            description += truncate_append

        description = description_formatter(description)

        self._description = description

    def add_field(self, *, name: Any, value: Any, max_field: Optional[int] = None, truncate_append: Optional[str] = None, value_formatter: Optional[Callable] = None, inline: Optional[bool] = None):
        if inline is None:
            inline = self.inline
        if value_formatter is None:
            value_formatter = self.field_formatter

        if value_formatter is None:
            value_formatter = formatter.blank_formatter

        if max_field is None:
            max_field = self.max_field

        if truncate_append is None:
            truncate_append = self.truncate_append

        if len(value) > self.max_description:
            value = value[:(max_field - len(truncate_append))]
            value += truncate_append

        value = value_formatter(value)

        return super().add_field(name=name, value=value, inline=inline)

    def set_author(
            self, *, name: Any, url: Optional[Any] = None, icon_url: Optional[Any] = None, author: Optional[discord.User] = None
    ):
        if author is None:
            return super(Embed, self).set_author(name=name, icon_url=icon_url, url=url)
        else:
            return super(Embed, self).set_author(name=author.display_name, icon_url=author.display_avatar.url, url=url)

    def sort_fields(self, key, reverse=False):
        fields = self.fields
        fields.sort(key=key, reverse=reverse)
        self.set_fields(fields)

    def to_base64(self) -> str:
        return base64.b64encode(json.dumps(self.to_dict()).encode('utf-8')).decode('utf-8')

    @classmethod
    def from_base64(cls, s: str):
        data = base64.b64decode(s.encode('utf-8')).decode('utf-8')
        data = json.loads(data)
        self = cls()

        # fill in the basic fields

        self.title = data.get('title', None)
        self.type = data.get('type', None)
        self.description = data.get('description', None)
        self.url = data.get('url', None)

        if self.title is not None:
            self.title = str(self.title)

        if self.description is not None:
            self.description = str(self.description)

        if self.url is not None:
            self.url = str(self.url)

        # try to fill in the more rich fields

        try:
            self._colour = discord.Colour(value=data['color'])
        except KeyError:
            pass

        try:
            self._timestamp = discord.utils.parse_time(data['timestamp'])
        except KeyError:
            pass

        for attr in ('thumbnail', 'video', 'provider', 'author', 'fields', 'image', 'footer'):
            try:
                value = data[attr]
            except KeyError:
                continue
            else:
                setattr(self, '_' + attr, value)

        return self

    def set_everything(self, val: bool):
        self.active_title = val
        self.active_description = val
        self.active_url = val
        self.active_image = val
        self.active_thumbnail = val
        self.active_author = val

    def set_active_title(self, val):
        self.active_title = val

    def set_active_description(self, val: bool):
        self.active_description = val

    def set_active_url(self, val: bool):
        self.active_url = val

    def set_active_image(self, val: bool):
        self.active_image = val

    def set_active_thumbnail(self, val: bool):
        self.active_thumbnail = val

    def set_active_author(self, val: bool):
        self.active_author = val

    def to_dict(self):
        data = super().to_dict()
        if not self.active_description:
            data.pop('description', None)
        if not self.active_url:
            data.pop('url', None)
        if not self.active_title:
            data.pop('title', None)
        if not self.active_author:
            data.pop('author', None)
        if not self.active_thumbnail:
            data.pop('thumbnail', None)
        if not self.active_image:
            data.pop('image', None)
        return data

    def set_title(self, title, url: Optional[str] = MISSING):
        self.title = title
        if url is not MISSING:
            self.url = url
