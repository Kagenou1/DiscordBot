"""Парсинг ответов yt-dlp в Track + регексы для распознавания YT-ссылок."""
import re

from ..track import Track


YTM_PLAYLIST_RE = re.compile(r'music\.youtube\.com/playlist\?.*\blist=([\w-]+)')
YTM_WATCH_RE = re.compile(r'music\.youtube\.com/watch\?.*\bv=([\w-]+)')


def entry_to_track(entry: dict, *, resolver) -> Track | None:
    """Превратить запись из yt-dlp (трек или flat-плейлист) в Track."""
    if entry is None:
        return None
    entry_url = entry.get('webpage_url') or entry.get('url')
    if not entry_url:
        return None
    artist = ''
    uploader = entry.get('uploader') or entry.get('channel') or ''
    if uploader:
        artist = re.sub(r'\s*-\s*Topic\s*$', '', uploader).strip()
    artists_field = entry.get('artists') or entry.get('artist')
    if isinstance(artists_field, list) and artists_field:
        artist = ', '.join(str(a) for a in artists_field) or artist
    elif isinstance(artists_field, str) and artists_field:
        artist = artists_field
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
