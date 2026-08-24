"""Локальный сервер выдачи GVS PO Token

Зачем: с токеном YouTube отдаёт opus по ссылкам, валидным сразу. Без токена
единственный источник opus — клиент, чьи ссылки часть времени отвечают 403
первые секунды, и старт трека откладывается. Замер на 12 треках: медиана
2972 мс против 7020 мс, p90 3324 против 7516.

Почему именно сервер, а не скрипт на каждый запрос: токен привязан к видео,
то есть нужен на каждый трек, а дорогая часть — «чеканщик» BotGuard — живёт
в памяти процесса. Сервер строит его один раз (около секунды) и дальше выдаёт
токены за миллисекунды; скриптовый режим пересоздаёт его каждый раз и после
нескольких треков начинает отказывать, платя десятками секунд.

Плагин bgutil сам ходит на 127.0.0.1:4416, поэтому настраивать yt-dlp не нужно —
достаточно, чтобы сервер слушал.
"""
import atexit
import logging
import os
import socket
import subprocess
from pathlib import Path


_log = logging.getLogger('audio').info

_ROOT = Path(__file__).resolve().parents[2]
_SERVER_JS = _ROOT / 'third_party' / 'bgutil' / 'server' / 'build' / 'main.js'
_NODE_DIR = _ROOT / 'third_party' / 'node'
_LOCAL_NODE = _NODE_DIR / ('node.exe' if os.name == 'nt' else 'bin/node')

PORT = 4416
_proc: 'subprocess.Popen | None' = None


def _node() -> str | None:
    if _LOCAL_NODE.exists():
        return str(_LOCAL_NODE)
    from shutil import which
    return which('node')


def _port_busy(port: int = PORT) -> bool:
    """Сервер мог поднять кто-то другой — тогда переиспользуем его"""
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(('127.0.0.1', port)) == 0


def available() -> bool:
    return _SERVER_JS.is_file() and _node() is not None


def start() -> bool:
    """Поднять сервер, если он ещё не слушает. True — токены будут"""
    global _proc
    if _port_busy():
        _log('pot: сервер уже слушает, переиспользуем')
        return True
    if not available():
        return False
    node = _node()
    try:
        _proc = subprocess.Popen(
            [node, str(_SERVER_JS)],
            cwd=str(_SERVER_JS.parent.parent),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            # без своей группы Ctrl+C в консоли убивал бы сервер раньше бота
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0,
        )
    except Exception as exc:
        print(f'pot: сервер не запустился ({exc!r}), токенов не будет')
        return False
    atexit.register(stop)
    _log(f'pot: сервер запущен (pid {_proc.pid})')
    return True


def stop() -> None:
    global _proc
    if _proc is None:
        return
    proc, _proc = _proc, None
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def ready(timeout: float = 5.0) -> bool:
    """Дождаться, пока сервер начнёт отвечать: первый токен строит чеканщика"""
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_busy():
            return True
        time.sleep(0.1)
    return False
