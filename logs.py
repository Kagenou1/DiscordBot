"""Сессионное логирование с ежедневной чисткой.

Два уровня:
- *Процессная* сессия: `log/session-YYYY-MM-DD_HH-MM-SS.log` — зеркало
  stdout/stderr для всего, что бот пишет (logging + сырой print).
- *Гильдийная* сессия: `log/guild-{gid}-YYYY-MM-DD_HH-MM-SS.log` — события
  воспроизведения конкретного сервера, открывается на /join, закрывается на /leave.

Чистка раз в сутки удаляет любые файлы из `log/` старше 24 ч.
"""
import asyncio
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path


LOG_DIR = Path(__file__).resolve().parent / 'log'
RETENTION_SECONDS = 24 * 60 * 60


class _Tee:
    """Минимальный stream-сплиттер для sys.stdout/sys.stderr."""

    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for s in self._streams:
            try:
                s.write(data)
            except Exception:
                pass

    def flush(self):
        for s in self._streams:
            try:
                s.flush()
            except Exception:
                pass


def _cleanup_old_logs() -> int:
    if not LOG_DIR.exists():
        return 0
    cutoff = time.time() - RETENTION_SECONDS
    removed = 0
    for f in LOG_DIR.glob('*.log'):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            pass
    return removed


_guild_files: dict[int, '_GuildFile'] = {}


_NAME_SAFE = re.compile(r'[^\w.-]+', re.UNICODE)


class _GuildFile:
    __slots__ = ('path', 'fp')

    def __init__(self, gid: int, guild_name: str):
        safe = _NAME_SAFE.sub('_', guild_name or '')[:40].strip('_') or 'guild'
        ts = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        self.path = LOG_DIR / f'guild-{gid}-{safe}-{ts}.log'
        self.fp = open(self.path, 'a', encoding='utf-8', buffering=1)
        self.fp.write(f'# {guild_name} ({gid}) — session started {ts}\n')

    def write(self, msg: str) -> None:
        ts = datetime.now().strftime('%H:%M:%S')
        line = msg if msg.endswith('\n') else msg + '\n'
        try:
            self.fp.write(f'{ts} {line}')
        except Exception:
            pass

    def close(self) -> None:
        try:
            self.fp.close()
        except Exception:
            pass


def open_guild_session(gid: int, guild_name: str = '') -> None:
    """Открыть файл-сессию для гильдии. Повторный вызов на ту же gid — no-op."""
    if gid in _guild_files:
        return
    LOG_DIR.mkdir(exist_ok=True)
    _guild_files[gid] = _GuildFile(gid, guild_name)


def close_guild_session(gid: int) -> None:
    gf = _guild_files.pop(gid, None)
    if gf is not None:
        gf.write('-- session closed')
        gf.close()


def session_log(gid: int, msg: str) -> None:
    """Записать событие в файл гильдии (если он открыт). Иначе — no-op."""
    gf = _guild_files.get(gid)
    if gf is not None:
        gf.write(msg)


def setup_logging() -> Path:
    """Создаёт новый сессионный файл, удаляет старее суток, конфигурит logging."""
    LOG_DIR.mkdir(exist_ok=True)
    _cleanup_old_logs()
    session = LOG_DIR / f'session-{datetime.now():%Y-%m-%d_%H-%M-%S}.log'
    # buffering=1 -> line-buffered: каждый \n флашится сразу, не теряем хвост при крэше
    fp = open(session, 'a', encoding='utf-8', buffering=1)
    sys.stdout = _Tee(sys.__stdout__, fp)
    sys.stderr = _Tee(sys.__stderr__, fp)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%H:%M:%S',
    )
    for noisy in ('discord.voice_client', 'discord.player', 'discord.gateway'):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return session


async def daily_cleanup_loop() -> None:
    """Фоновый таск: раз в сутки удаляет файлы старше суток."""
    while True:
        await asyncio.sleep(RETENTION_SECONDS)
        try:
            _cleanup_old_logs()
        except Exception as exc:
            print(f'log cleanup error: {exc!r}')
