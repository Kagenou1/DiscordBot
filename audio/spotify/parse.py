"""Парсинг ответов Spotify Web API в Track и регексы ссылок"""
import re

from ..track import Track


# «Поделиться» отдаёт ссылку с языковым сегментом open.spotify.com/intl-ru/track/...,
# сегмент опционален и в идентификатор не входит
_BASE = r'open\.spotify\.com/(?:intl-[\w-]+/)?'

SPOTIFY_TRACK_RE = re.compile(_BASE + r'track/(\w+)')
SPOTIFY_ALBUM_RE = re.compile(_BASE + r'album/(\w+)')
SPOTIFY_PLAYLIST_RE = re.compile(_BASE + r'playlist/(\w+)')


def playlist_entry_item(entry: dict | None) -> dict | None:
    """Достать track-объект из элемента плейлиста

    Ответ /playlists/{id}/items кладёт трек в поле item, в документации поле
    называется track. Читаем оба, чтобы не зависеть от смены имени
    """
    if not entry:
        return None
    return entry.get('item') or entry.get('track')


def item_to_track(item: dict, *, resolver) -> Track | None:
    """track-объект Spotify Web API -> Track

    В Track.url остаётся ссылка на Spotify для логов и дедупа, стрим добывает
    resolver поиском на YouTube по title и artist
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
