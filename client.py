import asyncio
import atexit
import ctypes
import ctypes.wintypes as wintypes
import hashlib
import json
import logging
import sys
from pathlib import Path

try:
    import winloop
    winloop.install()
except ImportError:
    pass

import discord
from discord.ext import commands

from logs import daily_cleanup_loop, setup_logging

setup_logging()


def _boost_process_priority():
    """Поднять приоритет процесса: поток плеера чувствителен к вытеснению

    GetCurrentProcess отдаёт псевдохендл (HANDLE)-1. Без явного restype ctypes
    трактует результат как c_int и на x64 передаёт его усечённым, из-за чего
    SetPriorityClass получал невалидный хендл и возвращал 0 с ошибкой 6.
    GetLastError тоже читаем через ctypes: windll.kernel32.GetLastError()
    отдаёт мусор, так как ctypes сохраняет и восстанавливает код вокруг вызовов
    """
    if sys.platform != 'win32':
        return
    ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000
    try:
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.SetPriorityClass.restype = wintypes.BOOL
        if not k32.SetPriorityClass(k32.GetCurrentProcess(), ABOVE_NORMAL_PRIORITY_CLASS):
            logging.warning('SetPriorityClass failed (error %s)', ctypes.get_last_error())
    except Exception as exc:
        logging.warning(f'failed to boost process priority: {exc!r}')


def _enable_high_res_timer():
    """1 мс разрешение системного таймера: без него sleep в потоке плеера гуляет на ~15 мс"""
    if sys.platform != 'win32':
        return
    try:
        ctypes.windll.winmm.timeBeginPeriod(1)
    except Exception as exc:
        logging.warning(f'failed to enable high-res timer: {exc!r}')
        return
    # timeBeginPeriod требует парного вызова, иначе режим держится до конца процесса
    atexit.register(_disable_high_res_timer)


def _disable_high_res_timer():
    try:
        ctypes.windll.winmm.timeEndPeriod(1)
    except Exception:
        pass


_boost_process_priority()
_enable_high_res_timer()


from audio import warm_up
from private import discord_bot_token, prefix
from widgets import set_emojis
from widgets import plain_error


intents = discord.Intents.default()
intents.message_content = True


# слепок набора слэш-команд: глобальный sync жёстко лимитируется Discord,
# поэтому дёргаем его только когда команды реально изменились;
# лежит в log/, который уже вне репозитория
_SYNC_STATE = Path(__file__).resolve().parent / 'log' / '.commands-fingerprint'


def _param_shape(param) -> tuple:
    """Всё, что Discord показывает пользователю у параметра

    Одного имени мало: правка описания или списка choices до Discord не доедет,
    отпечаток не изменится, и пользователь будет видеть старую подсказку
    """
    return (
        param.name,
        getattr(param, 'description', ''),
        bool(getattr(param, 'required', False)),
        tuple(sorted((getattr(ch, 'name', ''), str(getattr(ch, 'value', '')))
                     for ch in getattr(param, 'choices', ()) or ())),
    )


def _commands_fingerprint(tree: discord.app_commands.CommandTree) -> str:
    payload = sorted(
        (c.name, c.description,
         tuple(sorted(_param_shape(p) for p in getattr(c, 'parameters', ()))))
        for c in tree.get_commands()
    )
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()


class MusicBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # loop держит на задачу только слабую ссылку, без этого её может собрать GC
        self._bg_tasks: set[asyncio.Task] = set()

    def _spawn(self, coro):
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task

    async def setup_hook(self):
        await self.load_extension('cogs.music')
        self._spawn(warm_up(asyncio.get_running_loop()))
        self._spawn(daily_cleanup_loop())
        self._spawn(self._load_service_emojis())
        await self._sync_if_changed()

    async def _load_service_emojis(self):
        """Подтянуть иконки сервисов, загруженные tools/upload_emoji.py

        Фоном и молча при отказе: без иконок строка источника остаётся
        текстовой, а воспроизведение это не трогает вовсе
        """
        try:
            emojis = await self.fetch_application_emojis()
        except Exception as exc:
            logging.warning(f'иконки сервисов не загрузились: {exc!r}')
            return
        set_emojis({e.name: str(e) for e in emojis})
        logging.info(f'иконок сервисов: {len(emojis)}')

    async def _sync_if_changed(self):
        fingerprint = _commands_fingerprint(self.tree)
        try:
            previous = _SYNC_STATE.read_text(encoding='utf-8').strip()
        except OSError:
            previous = ''
        if previous == fingerprint:
            print('Slash commands unchanged, sync skipped')
            return
        synced = await self.tree.sync()
        try:
            _SYNC_STATE.parent.mkdir(exist_ok=True)
            _SYNC_STATE.write_text(fingerprint, encoding='utf-8')
        except OSError as exc:
            logging.warning(f'failed to persist command fingerprint: {exc!r}')
        print(f'Synced {len(synced)} slash commands')


bot = MusicBot(
    command_prefix=commands.when_mentioned_or(prefix) if prefix else commands.when_mentioned,
    description='Музыкальный бот для воспроизведения с YouTube, Spotify',
    intents=intents,
)


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')


@bot.event
async def on_command_error(ctx, error):
    """Без этого ошибка команды уходит трейсбеком в консоль, а пользователь видит тишину"""
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CommandError) and str(error) == 'Author not connected to a voice channel.':
        return  # текст уже отправлен в _ensure_voice
    original = getattr(error, 'original', error)
    logging.exception(f'command {ctx.command} failed', exc_info=original)
    try:
        await ctx.send(f'Ошибка команды: {plain_error(original)}')
    except discord.HTTPException:
        pass


async def main():
    async with bot:
        await bot.start(discord_bot_token)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # asyncio.run уже отменил главную задачу, bot.close() отработал через async with
        print('Остановлена (Ctrl+C)')
