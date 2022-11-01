import asyncio
import importlib
import logging
import traceback

import bot as bot_global
from bot.util import config
from bot.wormhole import Wormhole
import pathlib
from bot.util import database as db
from bot.wormhole import startup_extensions


class RemoveNoise(logging.Filter):
    def __init__(self):
        super().__init__(name='discord.http')

    def filter(self, record):
        if record.levelname == 'WARNING' and 'We are being rate limited.' in record.msg:
            return False
        return True


logging.getLogger().setLevel(logging.INFO)
logging.getLogger('discord').setLevel(logging.INFO)
logging.getLogger('discord.client').setLevel(logging.WARNING)
logging.getLogger('discord.gateway').setLevel(logging.WARNING)
logging.getLogger('discord.http').setLevel(logging.WARNING)
logging.getLogger('discord.http').addFilter(RemoveNoise())


async def create_tables(connection):
    for table in db.Table.all_tables():
        try:
            await table.create(connection=connection)
        except Exception:     # noqa: E722
            logging.warning('Failed creating table {0}'.format(table.tablename))
            traceback.print_exc()


async def database(pool):

    cogs = startup_extensions

    for cog in cogs:
        try:
            importlib.import_module('{0}'.format(cog))
        except Exception:     # noqa: E722
            logging.warning('Could not load {0}'.format(cog))
            traceback.print_exc()
            return

    logging.info('Preparing to create {0} tables.'.format(len(db.Table.all_tables())))

    async with pool.acquire() as con:
        await create_tables(con)


async def run_bot():
    bot_global.config = config.Config(pathlib.Path('config.toml'))

    loop = asyncio.get_event_loop()
    log = logging.getLogger()
    kwargs = {
        'command_timeout': 60,
        'max_size': 20,
        'min_size': 20,
    }
    url = 'postgresql://{1}:{2}@localhost/{0}'.format(
        bot_global.config['postgresql_name'],
        bot_global.config['postgresql_user'],
        bot_global.config['postgresql_password'],
    )
    try:
        pool = await db.Table.create_pool(url, **kwargs)
        await database(pool)
    except Exception as e:
        log.exception('Could not set up PostgreSQL. Exiting.')
        return

    bot = Wormhole(pool)
    await bot.start()


if __name__ == '__main__':
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        exit()
