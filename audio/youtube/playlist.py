"""Быстрый путь для плейлистов YT Music — JSON API вместо yt-dlp."""
from ..track import Track
from .client import ytm


def ytm_playlist_tracks(playlist_id: str, *, resolver, limit: int | None = None) -> list[Track]:
    if ytm is None:
        raise RuntimeError('ytmusicapi недоступен.')
    data = ytm.get_playlist(playlist_id, limit=limit)
    tracks: list[Track] = []
    for item in data.get('tracks') or []:
        vid = item.get('videoId')
        title = item.get('title')
        if not vid or not title:
            continue
        artists = item.get('artists') or []
        artist = ', '.join(a.get('name', '') for a in artists if a.get('name'))
        tracks.append(Track(
            url=f'https://music.youtube.com/watch?v={vid}',
            title=title,
            artist=artist,
            resolver=resolver,
        ))
    return tracks
