"""Парсинг ответов Spotify Web API в Track + регексы для распознавания ссылок."""
import re

from ..track import Track


SPOTIFY_TRACK_RE = re.compile(r'open\.spotify\.com/track/([\w]+)')
SPOTIFY_ALBUM_RE = re.compile(r'open\.spotify\.com/album/([\w]+)')
SPOTIFY_PLAYLIST_RE = re.compile(r'open\.spotify\.com/playlist/([\w]+)')


def item_to_track(item: dict, *, resolver) -> Track | None:
    """Превратить track-объект Spotify Web API в Track.

    У Track.url остаётся ссылка на Spotify (для логов/дедупа), реальный стрим
    добывает resolver через поиск на YouTube по title+artist.
    """
    if not item:
        return None
    title = item.get('name')
    if not title:
        return None
    artists = item.get('artists') or []
    artist = ', '.join(a.get('name', '') for a in artists if a.get('name'))
    spotify_url = (item.get('external_urls') or {}).get('spotify') or ''
    images = ((item.get('album') or {}).get('images') or []) or item.get('images') or []
    thumbnail = images[0].get('url', '') if images else ''
    duration = (item.get('duration_ms') or 0) / 1000.0
    return Track(
        url=spotify_url,
        title=title,
        artist=artist,
        thumbnail=thumbnail,
        duration=duration,
        resolver=resolver,
    )
