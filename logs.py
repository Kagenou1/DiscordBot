"""Сессионное логирование с ежедневной чисткой

Два уровня:
- процессная сессия: log/session-YYYY-MM-DD_HH-MM-SS.log — зеркало stdout/stderr
  для всего, что пишет бот (logging и сырой print)
- гильдийная сессия: log/guild-{gid}-YYYY-MM-DD_HH-MM-SS.log — события
  воспроизведения сервера, открывается на /join, закрывается на /leave

Чистка раз в сутки удаляет файлы старше 24 ч, кроме открытых прямо сейчас
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
    """Stream-сплиттер для sys.stdout/sys.stderr

    Подменяет стандартные потоки, поэтому обязан выглядеть как файловый объект:
    сторонние библиотеки дёргают isatty/encoding/fileno и ждут int от write
    """

    def __init__(self, *streams):
        self._streams = streams

    @property
    def _primary(self):
        return self._streams[0] if self._streams else None

    def write(self, data) -> int:
        for s in self._streams:
            try:
                s.write(data)
            except Exception:
                pass
        return len(data)

    def writelines(self, lines) -> None:
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        for s in self._streams:
            try:
                s.flush()
            except Exception:
                pass

    def isatty(self) -> bool:
        return bool(getattr(self._primary, 'isatty', lambda: False)())

    def fileno(self) -> int:
        primary = self._primary
        if primary is None or not hasattr(primary, 'fileno'):
            raise OSError('_Tee has no underlying file descriptor')
        return primary.fileno()

    def writable(self) -> bool:
        return True

    def readable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return False

    @property
    def encoding(self) -> str:
        return getattr(self._primary, 'encoding', 'utf-8') or 'utf-8'

    @property
    def errors(self) -> str:
        return getattr(self._primary, 'errors', 'replace') or 'replace'

    @property
    def closed(self) -> bool:
        return False


_guild_files: dict[int, '_GuildFile'] = {}


_NAME_SAFE = re.compile(r'[^\w.-]+', re.UNICODE)


def _open_paths() -> set[Path]:
    return {gf.path for gf in _guild_files.values()}


def _cleanup_old_logs() -> int:
    if not LOG_DIR.exists():
        return 0
    cutoff = time.time() - RETENTION_SECONDS
    protected = _open_paths()
    removed = 0
    for f in LOG_DIR.glob('*.log'):
        # открытый файл гильдии может быть старше суток, если в него давно не писали
        if f in protected:
            continue
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            pass
    return removed


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
    """Открыть файл-сессию гильдии, повторный вызов на ту же gid — no-op"""
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
    """Записать событие в файл гильдии, если он открыт"""
    gf = _guild_files.get(gid)
    if gf is not None:
        gf.write(msg)


def _force_utf8_console() -> None:
    """Перевести реальные stdout/stderr в UTF-8

    В настоящем терминале Windows пишет через WriteConsoleW и кодировка уже UTF-8,
    но под пайпом или перенаправлением Python берёт кодировку локали (cp1251),
    и строки с CJK-названиями теряются
    """
    for stream in (sys.__stdout__, sys.__stderr__):
        if stream is None or not hasattr(stream, 'reconfigure'):
            continue
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (OSError, ValueError):
            pass


def setup_logging() -> Path:
    """Создать сессионный файл, удалить старее суток, сконфигурировать logging"""
    _force_utf8_console()
    LOG_DIR.mkdir(exist_ok=True)
    _cleanup_old_logs()
    session = LOG_DIR / f'session-{datetime.now():%Y-%m-%d_%H-%M-%S}.log'
    # buffering=1 — построчный флаш, хвост не теряется при крэше
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
    """Раз в сутки удаляет файлы старше суток"""
    while True:
        await asyncio.sleep(RETENTION_SECONDS)
        try:
            _cleanup_old_logs()
        except Exception as exc:
            print(f'log cleanup error: {exc!r}')
