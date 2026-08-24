"""yt-dlp под VK Музыку: куки сессии, маршрут и id пользователя

Экстрактор регистрируется здесь же через add_info_extractor, а не плагином:
плагин пришлось бы держать каталогом в корне проекта, и yt-dlp импортирует его
своим загрузчиком отдельным экземпляром модуля.

Без vk_cookies провайдер выключается. VK без кук отдаёт 31 секунду при полной
заявленной длительности и никак это не помечает, то есть молча ломает
воспроизведение
"""
import json
import logging
import os
import re

import yt_dlp as youtube_dl

from .extractor import VKMusicPlaylistIE, VKMusicTrackIE, _DOMAIN, _UA

try:
    from private import vk_cookies
except ImportError:
    vk_cookies = ''
try:
    from private import vk_proxy
except ImportError:
    vk_proxy = ''


_log = logging.getLogger('audio').info

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ID_CACHE_PATH = os.path.join(_ROOT, 'log', '.vk-user-id')

# маркеры id на странице /audios; на /feed те же дают чужие значения
_ID_PATTERNS = (r'"uid"\s*:\s*(\d+)', r'"user_id"\s*:\s*(\d+)', r'"ownerId"\s*:\s*(\d+)')

_YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': False,
    'extract_flat': False,
    'nocheckcertificate': True,
    'no_warnings': True,
    'quiet': True,
    'socket_timeout': 15,
    'retries': 2,
    'extractor_retries': 2,
}


def _read_cached_id() -> int:
    try:
        with open(_ID_CACHE_PATH, encoding='utf-8') as fh:
            return int(json.load(fh)['user_id'])
    except Exception:
        return 0


def _write_cached_id(user_id: int) -> None:
    try:
        os.makedirs(os.path.dirname(_ID_CACHE_PATH), exist_ok=True)
        with open(_ID_CACHE_PATH, 'w', encoding='utf-8') as fh:
            json.dump({'user_id': user_id}, fh)
    except OSError as exc:
        _log(f'vk: id не сохранился: {exc!r}')


def fetch_user_id() -> int:
    """Определить id владельца кук запросом к /audios

    Своим клиентом, а не загрузчиком yt-dlp: тому VK отдаёт страницу без
    маркеров — 136 КБ против 293 при тех же куках и заголовках
    """
    import http.cookiejar as cookiejar

    import requests

    jar = cookiejar.MozillaCookieJar(vk_cookies)
    jar.load(ignore_discard=True, ignore_expires=True)
    session = requests.Session()
    session.cookies = jar
    proxies = {'http': vk_proxy, 'https': vk_proxy} if vk_proxy else None
    page = session.get(f'{_DOMAIN}/audios', headers={'User-Agent': _UA},
                       proxies=proxies, timeout=25).text

    found = set()
    for pattern in _ID_PATTERNS:
        found.update(int(x) for x in re.findall(pattern, page))
    if len(found) != 1:
        raise RuntimeError(
            'Сессия VK не признана' if not found else 'Маркеры id разошлись')
    return found.pop()


def build(user_id: int, *, flat: bool = False) -> 'youtube_dl.YoutubeDL':
    """flat — для плейлистов: без него yt-dlp резолвит каждый трек сразу,
    то есть запрос к al_audio на каждую позицию ещё при добавлении в очередь"""
    options = dict(_YTDL_OPTIONS, cookiefile=vk_cookies,
                   extract_flat='in_playlist' if flat else False,
                   extractor_args={'vkmusic': {'user_id': [str(user_id)]}})
    if vk_proxy:
        options['proxy'] = vk_proxy
    ytdl = youtube_dl.YoutubeDL(options)
    for extractor in (VKMusicTrackIE, VKMusicPlaylistIE):
        ytdl.add_info_extractor(extractor(ytdl))
    # add_info_extractor дописывает в конец, а Generic уже лежит там и подходит
    # под любой URL: без перестановки ссылки VK уходят ему и падают Unsupported
    generic = ytdl._ies.pop('Generic', None)
    if generic is not None:
        ytdl._ies['Generic'] = generic
    return ytdl


def configured() -> bool:
    if not vk_cookies:
        return False
    if not os.path.isfile(vk_cookies):
        print(f'Файла кук VK нет: {vk_cookies} — провайдер выключен.')
        return False
    return True


def ensure() -> bool:
    """Достроить клиенты; False — провайдер остаётся выключенным

    Зовётся из прогрева, а не при импорте: id при первом запуске добывается
    запросом, а маршрут к тому моменту должен быть уже поднят
    """
    global vk_ytdl, vk_flat_ytdl
    if vk_ytdl is not None:
        return True
    if not configured():
        return False
    user_id = _read_cached_id()
    if not user_id:
        try:
            user_id = fetch_user_id()
        except Exception as exc:
            print(f'VK: не удалось определить id пользователя ({exc}) — '
                  f'провайдер выключен.')
            return False
        _write_cached_id(user_id)
    vk_ytdl = build(user_id)
    vk_flat_ytdl = build(user_id, flat=True)
    _log(f'vk client ready (user {user_id})')
    return True


vk_ytdl = None
vk_flat_ytdl = None

if not vk_cookies:
    print('vk_cookies не задан в private.py — провайдер VK Музыки выключен.')
