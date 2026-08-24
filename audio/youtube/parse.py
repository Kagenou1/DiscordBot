"""Парсинг ответов yt-dlp в Track и регексы YT-ссылок"""
import re

from ..track import Track


YTM_PLAYLIST_RE = re.compile(r'music\.youtube\.com/playlist\?.*\blist=([\w-]+)')
YTM_WATCH_RE = re.compile(r'music\.youtube\.com/watch\?.*\bv=([\w-]+)')

# yt-dlp нормализует хост: music.youtube.com/watch?v=X отдаётся как
# www.youtube.com/watch?v=X. Ссылка на названии трека тогда уводит с YT Music
# на YouTube, а source_label подписывает карточку не тем источником
_YT_HOST_RE = re.compile(r'^https?://(?:www\.|m\.)?youtube\.com/')


def as_music_url(url: str) -> str:
    """Ссылку на youtube.com вернуть в виде music.youtube.com"""
    return _YT_HOST_RE.sub('https://music.youtube.com/', url or '', count=1)


def entry_to_track(entry: dict, *, resolver) -> Track | None:
    """Запись yt-dlp (трек или элемент flat-плейлиста) -> Track"""
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
    # duration нужен скорингу YT Music; во flat-режиме приходит не всегда
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
