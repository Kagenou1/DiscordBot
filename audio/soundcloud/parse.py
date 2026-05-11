"""Парсинг ответов yt-dlp в Track + регексы для распознавания SoundCloud-ссылок."""
import re

from ..track import Track


# Сеты: soundcloud.com/<user>/sets/<slug>
SC_SET_RE = re.compile(r'soundcloud\.com/[^/]+/sets/[^/?#]+')
# Любая SoundCloud-ссылка (роутинг происходит в audio/__init__.py)
SC_ANY_RE = re.compile(r'(?:^|\W)(?:on\.|m\.)?soundcloud\.com/')


def entry_to_track(entry: dict, *, resolver) -> Track | None:
    """Превратить запись из yt-dlp (трек или flat-элемент сета) в Track."""
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
    return Track(
        url=entry_url,
        title=entry.get('title') or 'Без названия',
        artist=artist,
        thumbnail=thumbnail,
        resolver=resolver,
    )
