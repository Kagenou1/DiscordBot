"""Загрузка плейлиста/альбома Spotify через Web API."""
from ..track import PlaylistInfo, Track
from .client import sp
from .parse import item_to_track


_PAGE = 100


def _images_to_thumbnail(images: list | None) -> str:
    if not images:
        return ''
    return images[0].get('url', '') if isinstance(images[0], dict) else ''


def spotify_playlist_info(playlist_id: str, *, resolver, limit: int | None = None) -> PlaylistInfo:
    if sp is None:
        raise RuntimeError('spotipy недоступен.')
    meta = sp.playlist(playlist_id, fields='name,external_urls,images')
    pl_title = (meta or {}).get('name', '') if meta else ''
    pl_url = ((meta or {}).get('external_urls') or {}).get('spotify', '') if meta else ''
    pl_thumb = _images_to_thumbnail((meta or {}).get('images')) if meta else ''

    tracks: list[Track] = []
    offset = 0
    while True:
        page = sp.playlist_items(
            playlist_id,
            limit=_PAGE,
            offset=offset,
            additional_types=('track',),
        )
        items = page.get('items') or []
        for entry in items:
            track = item_to_track(entry.get('item'), resolver=resolver)
            if track:
                tracks.append(track)
                if limit is not None and len(tracks) >= limit:
                    return PlaylistInfo(tracks=tracks, title=pl_title, url=pl_url, thumbnail=pl_thumb, kind='playlist')
        if not page.get('next'):
            break
        offset += _PAGE

    if not pl_thumb and tracks:
        pl_thumb = tracks[0].thumbnail
    return PlaylistInfo(tracks=tracks, title=pl_title, url=pl_url, thumbnail=pl_thumb, kind='playlist')


def spotify_album_info(album_id: str, *, resolver, limit: int | None = None) -> PlaylistInfo:
    if sp is None:
        raise RuntimeError('spotipy недоступен.')
    album = sp.album(album_id)
    album_url = (album.get('external_urls') or {}).get('spotify') or ''
    album_title = album.get('name') or ''
    album_thumb = _images_to_thumbnail(album.get('images'))

    tracks: list[Track] = []
    offset = 0
    while True:
        page = sp.album_tracks(album_id, limit=50, offset=offset)
        items = page.get('items') or []
        for item in items:
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
        if not page.get('next'):
            break
        offset += 50

    return PlaylistInfo(tracks=tracks, title=album_title, url=album_url, thumbnail=album_thumb, kind='album')
