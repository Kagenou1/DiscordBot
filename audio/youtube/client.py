"""Инициализация yt-dlp и ytmusicapi, синглтоны на процесс"""
import logging
import os
import time
from pathlib import Path

import yt_dlp as youtube_dl


_log = logging.getLogger('audio').info


# локальный бинарник в third_party/ или фолбэк на PATH-lookup
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LOCAL_DENO = _PROJECT_ROOT / 'third_party' / ('deno.exe' if os.name == 'nt' else 'deno')
deno_path = str(_LOCAL_DENO) if _LOCAL_DENO.exists() else 'deno'

_deno_dir = os.path.dirname(deno_path)
if _deno_dir and _deno_dir not in os.environ.get('PATH', '').split(os.pathsep):
    os.environ['PATH'] = _deno_dir + os.pathsep + os.environ.get('PATH', '')


try:
    from ytmusicapi import YTMusic
    ytm: 'YTMusic | None' = YTMusic()
except Exception as _exc:
    print(f'ytmusicapi unavailable: {_exc!r}')
    ytm = None


_ytdl_format_options = {
    'format': 'bestaudio[acodec=opus]/bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': False,
    'extract_flat': 'in_playlist',
    'nocheckcertificate': True,
    'ignoreerrors': True,
    'no_warnings': True,
    'quiet': True,
    'default_search': 'ytsearch',
    'socket_timeout': 15,
    'retries': 2,
    'extractor_retries': 2,
    # 'cookiefile': 'cookies.txt',
    'concurrent_fragment_downloads': 4,
    'remote_components': ['ejs:github'],
    # заполняется из _CLIENT_PROFILES ниже
    'extractor_args': {},
}
youtube_dl.utils.bug_reports_message = lambda *args, **kwargs: ''
ytdl = youtube_dl.YoutubeDL(_ytdl_format_options)


# --- профили клиентов YouTube ----------------------------------------------
# YouTube периодически перестаёт обслуживать конкретного player_client, поэтому
# профиль не захардкожен, а переключается на лету при сбое воспроизведения.
#
# Список внутри профиля — НЕ цепочка запасных. yt-dlp выгребает его целиком
# (while clients: clients.pop() в _video.py) и сливает форматы, раннего выхода
# при успехе нет. Каждый лишний клиент — плата на каждом треке.
#
# Все три профиля обходятся БЕЗ GVS PO Token намеренно. Токен разблокировал бы
# opus у быстрых клиентов, но его генератор привязывает токен к конкретному
# видео, то есть нужен на каждый трек, и после нескольких подряд начинает
# отказывать — замер дал 50 с на трек начиная с седьмого. Подробности в TODO
_CLIENT_PROFILES: tuple[tuple[str, list[str]], ...] = (
    # С сервером токенов (audio/youtube/pot.py) web_music отдаёт opus по
    # ссылкам, валидным сразу: замер на 12 треках дал медиану 2972 мс и p90
    # 3324 мс против 7020 и 7516 у web_embedded. Без сервера деградирует до
    # mp4a — перекодирование, но ссылки всё равно валидны сразу
    ('web_music', ['web_music']),
    # запасной без токена вовсе: opus ценой ожидания валидности ссылки.
    # android добирает треки, где web_embedded пуст (24/30 против 30/30)
    ('web_embedded', ['web_embedded', 'android']),
    ('android', ['android']),
)

# не переключаемся чаще, иначе одно битое видео прокрутит все профили подряд
_ROTATE_COOLDOWN = 30.0

_active = 0
_last_rotate = 0.0


def _apply_profile(index: int) -> None:
    # YoutubeDL читает extractor_args из params на каждом извлечении,
    # поэтому профиль меняется без пересоздания экземпляра
    ytdl.params['extractor_args'] = {
        'youtube': {'player_client': list(_CLIENT_PROFILES[index][1])},
    }


def active_profile() -> str:
    return _CLIENT_PROFILES[_active][0]


def rotate_profile() -> str | None:
    """Перейти к следующему профилю. None, если переключались только что"""
    global _active, _last_rotate
    now = time.monotonic()
    if now - _last_rotate < _ROTATE_COOLDOWN:
        return None
    _last_rotate = now
    _active = (_active + 1) % len(_CLIENT_PROFILES)
    _apply_profile(_active)
    _log(f'stream client profile -> {active_profile()}')
    return active_profile()


def extract_info(url: str, *, rotate_on_empty: bool = True):
    """extract_info с переключением профиля при пустом результате

    ignoreerrors=True превращает "нет подходящих форматов" в None, а вызывающий
    код трактовал None как недоступное видео и пропускал трек. Пока профиль был
    из пяти клиентов, это почти не всплывало: кто-нибудь да отдавал формат.
    С коротким профилем отсутствие форматов — обычное дело (web_embedded пуст
    на 6 треках из 30), и его надо лечить сменой профиля, а не пропуском
    """
    data = ytdl.extract_info(url, download=False)
    if data or not rotate_on_empty:
        return data
    was = active_profile()
    if rotate_profile() is None:
        return data
    _log(f'extract_info empty on {was}, retry on {active_profile()}')
    return ytdl.extract_info(url, download=False)


_apply_profile(_active)
