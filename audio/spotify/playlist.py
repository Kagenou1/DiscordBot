"""Загрузка плейлиста/альбома Spotify через Web API."""
from ..track import Track
from .client import sp
from .parse import item_to_track


_PAGE = 100


def spotify_playlist_tracks(playlist_id: str, *, resolver, limit: int | None = None) -> list[Track]:
    if sp is None:
        raise RuntimeError('spotipy недоступен.')
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
                    return tracks
        if not page.get('next'):
            break
        offset += _PAGE
    return tracks


def spotify_album_tracks(album_id: str, *, resolver, limit: int | None = None) -> list[Track]:
    if sp is None:
        raise RuntimeError('spotipy недоступен.')
    album = sp.album(album_id)
    album_url = (album.get('external_urls') or {}).get('spotify') or ''
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
            tracks.append(track)
            if limit is not None and len(tracks) >= limit:
                return tracks
        if not page.get('next'):
            break
        offset += 50
    return tracks
