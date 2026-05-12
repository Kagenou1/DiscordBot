"""Загрузка плейлиста/альбома Spotify через Web API."""
import asyncio

from ..track import PlaylistInfo, Track
from .client import sp
from .parse import item_to_track


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


async def _fetch_pages(loop, fetch, total: int, page_size: int) -> list[dict]:
    """Первая страница уже загружена снаружи; остальные тянем параллельно."""
    if total <= page_size:
        return []
    offsets = list(range(page_size, total, page_size))
    return await asyncio.gather(*[
        loop.run_in_executor(None, fetch, o) for o in offsets
    ])


async def spotify_playlist_info(playlist_id: str, *, resolver, loop, limit: int | None = None) -> PlaylistInfo:
    if sp is None:
        raise RuntimeError('spotipy недоступен.')

    meta_future = loop.run_in_executor(
        None, lambda: sp.playlist(playlist_id, fields='name,external_urls,images')
    )
    first = await loop.run_in_executor(None, _playlist_page, playlist_id, 0)
    total = int(first.get('total') or 0)
    rest = await _fetch_pages(loop, lambda o: _playlist_page(playlist_id, o), total, _PLAYLIST_PAGE)
    meta = await meta_future

    pl_title = (meta or {}).get('name', '') if meta else ''
    pl_url = ((meta or {}).get('external_urls') or {}).get('spotify', '') if meta else ''
    pl_thumb = _images_to_thumbnail((meta or {}).get('images')) if meta else ''

    tracks: list[Track] = []
    for page in (first, *rest):
        for entry in page.get('items') or []:
            track = item_to_track(entry.get('item'), resolver=resolver)
            if track is None:
                continue
            tracks.append(track)
            if limit is not None and len(tracks) >= limit:
                if not pl_thumb:
                    pl_thumb = tracks[0].thumbnail
                return PlaylistInfo(tracks=tracks, title=pl_title, url=pl_url, thumbnail=pl_thumb, kind='playlist')

    if not pl_thumb and tracks:
        pl_thumb = tracks[0].thumbnail
    return PlaylistInfo(tracks=tracks, title=pl_title, url=pl_url, thumbnail=pl_thumb, kind='playlist')


async def spotify_album_info(album_id: str, *, resolver, loop, limit: int | None = None) -> PlaylistInfo:
    if sp is None:
        raise RuntimeError('spotipy недоступен.')

    album = await loop.run_in_executor(None, lambda: sp.album(album_id))
    album_url = (album.get('external_urls') or {}).get('spotify') or ''
    album_title = album.get('name') or ''
    album_thumb = _images_to_thumbnail(album.get('images'))

    # sp.album уже отдаёт первые 50 треков под album['tracks']
    embedded = (album.get('tracks') or {}).get('items') or []
    total = int((album.get('tracks') or {}).get('total') or len(embedded))
    rest = await _fetch_pages(loop, lambda o: _album_page(album_id, o), total, _ALBUM_PAGE)

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
            return PlaylistInfo(tracks=tracks, title=album_title, url=album_url, thumbnail=album_thumb, kind='album')

    return PlaylistInfo(tracks=tracks, title=album_title, url=album_url, thumbnail=album_thumb, kind='album')
