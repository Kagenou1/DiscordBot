"""Локальный прокси для трафика VK, поднимается на прогреве

Нужен потому, что поток тянет ffmpeg отдельным процессом, а он понимает только
HTTP CONNECT. Через один локальный порт идут и запросы к API, и сам звук: VK
подписывает ссылку адресом запросившего, и разные выходы дают 403.

Запускается ИМЕНЕМ vkproxy.exe, а не python.exe: под Windows этот файл выводят
из VPN по имени, а python.exe — тот же, которым запущен бот, и его исключение
увело бы наружу весь трафик
"""
import atexit
import logging
import os
import socket
import subprocess
import sys
import urllib.parse
from pathlib import Path


_log = logging.getLogger('audio').info

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / 'tools' / 'vk_proxy.py'
_EXCLUDED = _ROOT / '.venv' / 'Scripts' / 'vkproxy.exe'

_proc: 'subprocess.Popen | None' = None


def port_of(url: str) -> int:
    try:
        return urllib.parse.urlparse(url).port or 0
    except ValueError:
        return 0


def _port_busy(port: int) -> bool:
    """Прокси мог поднять кто-то другой — тогда переиспользуем"""
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(('127.0.0.1', port)) == 0


def _runner() -> str:
    """vkproxy.exe, если он есть; иначе обычный интерпретатор"""
    if _EXCLUDED.is_file():
        return str(_EXCLUDED)
    return sys.executable


def start(url: str) -> bool:
    """Поднять прокси под url; True — трафик VK будет ходить через него"""
    global _proc
    port = port_of(url)
    if not port:
        _log(f'vk proxy: в адресе {url!r} нет порта')
        return False
    if _port_busy(port):
        _log(f'vk proxy: порт {port} уже слушает, переиспользуем')
        return True
    if not _SCRIPT.is_file():
        _log(f'vk proxy: нет {_SCRIPT}')
        return False
    try:
        _proc = subprocess.Popen(
            [_runner(), str(_SCRIPT), '--direct', '--port', str(port)],
            cwd=str(_ROOT),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0,
        )
    except Exception as exc:
        print(f'vk proxy: не запустился ({exc!r}), VK работать не будет')
        return False
    atexit.register(stop)
    _log(f'vk proxy: запущен на {port} (pid {_proc.pid})')
    return True


def stop() -> None:
    global _proc
    if _proc is None:
        return
    try:
        _proc.terminate()
        _proc.wait(timeout=3)
    except Exception:
        pass
    _proc = None
