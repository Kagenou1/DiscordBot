"""Регексы ссылок VK Музыки и разбор записей yt-dlp в Track"""
import re

from ..track import Track


# трек: vk.ru/audio-2001746599_34746599 либо с хешем доступа через _
VK_TRACK_RE = re.compile(r'vk\.(?:com|ru)/audio(-?\d+_\d+)(?:_[0-9a-f]+)?')
# плейлист и альбом, включая форму ?z=audio_playlist... на странице артиста
VK_PLAYLIST_RE = re.compile(
    r'vk\.(?:com|ru)/(?:music/(?:album|playlist)/|(?:.*[?&](?:act|z)=)?audio_playlist)'
    r'(-?\d+_\d+)')
# любая ссылка VK, роутинг в audio/__init__.py
VK_ANY_RE = re.compile(r'(?:^|\W)(?:(?:m|new)\.)?vk\.(?:com|ru)/')


def entry_to_track(entry: dict, *, resolver) -> Track | None:
    """Запись yt-dlp (трек или элемент плейлиста) -> Track

    У плоских записей плейлиста нет webpage_url, ссылка лежит в url
    """
    if entry is None:
        return None
    entry_url = entry.get('webpage_url') or entry.get('url')
    if not entry_url:
        return None
    thumbnail = entry.get('thumbnail') or ''
    if not thumbnail:
        thumbs = entry.get('thumbnails') or []
        if thumbs:
            thumbnail = thumbs[-1].get('url', '')
    try:
        duration = float(entry.get('duration') or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    # у VK title собран как "исполнитель - название", отдельное поле track чище
    title = entry.get('track') or entry.get('title') or 'Без названия'
    return Track(
        url=entry_url,
        title=title,
        duration=duration,
        artist=entry.get('artist') or entry.get('uploader') or '',
        thumbnail=thumbnail,
        resolver=resolver,
    )
