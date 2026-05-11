"""Парсинг ответов Yandex Music API в Track + регексы для распознавания ссылок."""
import re

from ..track import Track


YA_TRACK_RE = re.compile(r'music\.yandex\.(?:ru|by|kz|com)/(?:album/\d+/)?track/(\d+)')
YA_ALBUM_RE = re.compile(r'music\.yandex\.(?:ru|by|kz|com)/album/(\d+)')
YA_PLAYLIST_RE = re.compile(r'music\.yandex\.(?:ru|by|kz|com)/users/([^/]+)/playlists/(\d+)')
YA_PLAYLIST_UUID_RE = re.compile(r'music\.yandex\.(?:ru|by|kz|com)/playlists/([\w-]+)')


def cover_url(cover_uri: str | None, size: str = '400x400') -> str:
    """Превратить cover_uri вида 'avatars.yandex.net/.../%%' в полный URL."""
    if not cover_uri:
        return ''
    uri = cover_uri.replace('%%', size) if '%%' in cover_uri else cover_uri
    return uri if uri.startswith('http') else f'https://{uri}'


def ya_track_to_track(ya_track, *, resolver, album_id=None) -> Track | None:
    """Превратить yandex_music.Track в наш Track. None если трек недоступен."""
    if ya_track is None:
        return None
    if not getattr(ya_track, 'available', True):
        return None
    title = ya_track.title or 'Без названия'
    artists = ya_track.artists or []
    artist = ', '.join(a.name for a in artists if getattr(a, 'name', None))
    if album_id is None and ya_track.albums:
        album_id = ya_track.albums[0].id
    if album_id:
        url = f'https://music.yandex.ru/album/{album_id}/track/{ya_track.id}'
    else:
        url = f'https://music.yandex.ru/track/{ya_track.id}'
    return Track(url=url, title=title, artist=artist, resolver=resolver)
