"""Парсинг ответов yt-dlp в Track и регексы SoundCloud-ссылок"""
import re

from ..track import Track


# сеты: soundcloud.com/<user>/sets/<slug>
SC_SET_RE = re.compile(r'soundcloud\.com/[^/]+/sets/[^/?#]+')
# любая SoundCloud-ссылка, роутинг в audio/__init__.py
SC_ANY_RE = re.compile(r'(?:^|\W)(?:on\.|m\.)?soundcloud\.com/')


def entry_to_track(entry: dict, *, resolver) -> Track | None:
    """Запись yt-dlp (трек или элемент сета) -> Track"""
    if entry is None:
        return None
    entry_url = entry.get('webpage_url') or entry.get('url')
    if not entry_url:
        return None
    artist = entry.get('uploader') or entry.get('channel') or ''
    thumbnail = entry.get('thumbnail') or ''
    if not thumbnail:
        thumbs = entry.get('thumbnails') or []
        if thumbs:
            thumbnail = thumbs[-1].get('url', '')
    # duration нужен скорингу YT Music; приходит не всегда
    try:
        duration = float(entry.get('duration') or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return Track(
        url=entry_url,
        title=entry.get('title') or 'Без названия',
        duration=duration,
        artist=artist,
        thumbnail=thumbnail,
        resolver=resolver,
    )
