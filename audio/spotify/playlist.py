"""Загрузка плейлиста или альбома Spotify через Web API

Страницы тянутся последовательно: spotipy не потокобезопасен, параллельные
вызовы из пула потоков ловят гонку на обновлении токена и дают 401.
Один запрос отдаёт до 100 треков, плейлист на 1000 треков — десяток обращений
"""
from ..track import PlaylistInfo, Track
from .client import sp
from .parse import item_to_track, playlist_entry_item


_PLAYLIST_PAGE = 100
_ALBUM_PAGE = 50


def _images_to_thumbnail(images: list | None) -> str:
    if not images:
        return ''
    return images[0].get('url', '') if isinstance(images[0], dict) else ''


def _playlist_page(playlist_id: str, offset: int) -> dict:
    return sp.playlist_items(
        playlist_id,
        limit=_PLAYLIST_PAGE,
        offset=offset,
        additional_types=('track',),
    )


def _album_page(album_id: str, offset: int) -> dict:
    return sp.album_tracks(album_id, limit=_ALBUM_PAGE, offset=offset)


def _rest_pages(fetch, total: int, page_size: int) -> list[dict]:
    """Первая страница загружена снаружи, остальные тянем последовательно"""
    if total <= page_size:
        return []
    return [fetch(o) for o in range(page_size, total, page_size)]


def _load_playlist(playlist_id: str, *, resolver, limit: int | None) -> PlaylistInfo:
    """Синхронная часть, выполняется целиком в одном потоке executor'а"""
    if sp is None:
        raise RuntimeError('spotipy недоступен.')

    meta = sp.playlist(playlist_id, fields='name,external_urls,images')
    first = _playlist_page(playlist_id, 0)
    total = int(first.get('total') or 0)
    rest = _rest_pages(lambda o: _playlist_page(playlist_id, o), total, _PLAYLIST_PAGE)

    pl_title = (meta or {}).get('name', '') if meta else ''
    pl_url = ((meta or {}).get('external_urls') or {}).get('spotify', '') if meta else ''
    pl_thumb = _images_to_thumbnail((meta or {}).get('images')) if meta else ''

    tracks: list[Track] = []
    for page in (first, *rest):
        for entry in page.get('items') or []:
            track = item_to_track(playlist_entry_item(entry), resolver=resolver)
            if track is None:
                continue
            tracks.append(track)
            if limit is not None and len(tracks) >= limit:
                break
        if limit is not None and len(tracks) >= limit:
            break

    if not pl_thumb and tracks:
        pl_thumb = tracks[0].thumbnail
    return PlaylistInfo(tracks=tracks, title=pl_title, url=pl_url, thumbnail=pl_thumb, kind='playlist')


def _load_album(album_id: str, *, resolver, limit: int | None) -> PlaylistInfo:
    """Синхронная часть, выполняется целиком в одном потоке executor'а"""
    if sp is None:
        raise RuntimeError('spotipy недоступен.')

    album = sp.album(album_id)
    album_url = (album.get('external_urls') or {}).get('spotify') or ''
    album_title = album.get('name') or ''
    album_artist = ', '.join(a.get('name', '') for a in (album.get('artists') or []) if a.get('name'))
    album_thumb = _images_to_thumbnail(album.get('images'))

    # sp.album уже отдаёт первые 50 треков под album['tracks']
    embedded = (album.get('tracks') or {}).get('items') or []
    total = int((album.get('tracks') or {}).get('total') or len(embedded))
    rest = _rest_pages(lambda o: _album_page(album_id, o), total, _ALBUM_PAGE)

    tracks: list[Track] = []
    for item in (*embedded, *(it for page in rest for it in page.get('items') or [])):
        track = item_to_track(item, resolver=resolver)
        if track is None:
            continue
        if not track.url:
            track.url = album_url
        if not track.thumbnail:
            track.thumbnail = album_thumb
        tracks.append(track)
        if limit is not None and len(tracks) >= limit:
            break

    return PlaylistInfo(tracks=tracks, title=album_title, artist=album_artist,
                        url=album_url, thumbnail=album_thumb, kind='album')


async def spotify_playlist_info(playlist_id: str, *, resolver, loop, limit: int | None = None) -> PlaylistInfo:
    return await loop.run_in_executor(
        None, lambda: _load_playlist(playlist_id, resolver=resolver, limit=limit)
    )


async def spotify_album_info(album_id: str, *, resolver, loop, limit: int | None = None) -> PlaylistInfo:
    return await loop.run_in_executor(
        None, lambda: _load_album(album_id, resolver=resolver, limit=limit)
    )
