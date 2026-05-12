import asyncio
import ctypes
import logging
import sys

try:
    import winloop
    winloop.install()
except ImportError:
    pass

import discord
from discord.ext import commands

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)
logging.getLogger('discord.voice_client').setLevel(logging.WARNING)
logging.getLogger('discord.player').setLevel(logging.WARNING)
logging.getLogger('discord.gateway').setLevel(logging.WARNING)


def _boost_process_priority():
    if sys.platform != 'win32':
        return
    ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000
    try:
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ok = ctypes.windll.kernel32.SetPriorityClass(handle, ABOVE_NORMAL_PRIORITY_CLASS)
        if not ok:
            logging.warning('SetPriorityClass returned 0 (last error %s)', ctypes.windll.kernel32.GetLastError())
    except Exception as exc:
        logging.warning(f'failed to boost process priority: {exc!r}')


def _enable_high_res_timer():
    if sys.platform != 'win32':
        return
    try:
        ctypes.windll.winmm.timeBeginPeriod(1)
    except Exception as exc:
        logging.warning(f'failed to enable high-res timer: {exc!r}')


_boost_process_priority()
_enable_high_res_timer()


from audio import warm_up
from private import discord_bot_token, prefix


intents = discord.Intents.default()
intents.message_content = True


class MusicBot(commands.Bot):
    async def setup_hook(self):
        await self.load_extension('cogs.music')
        asyncio.create_task(warm_up(asyncio.get_running_loop()))
        synced = await self.tree.sync()
        print(f'Synced {len(synced)} slash commands')


bot = MusicBot(
    command_prefix=commands.when_mentioned_or(prefix),
    description='Музыкальный бот для воспроизведения с YouTube, Spotify',
    intents=intents,
)


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')


async def main():
    async with bot:
        await bot.start(discord_bot_token)


if __name__ == '__main__':
    asyncio.run(main())
