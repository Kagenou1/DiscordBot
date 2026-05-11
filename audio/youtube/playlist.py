"""Быстрый путь для плейлистов YT Music — JSON API вместо yt-dlp."""
from ..track import PlaylistInfo, Track
from .client import ytm


def _is_auto_yt_cover(url: str) -> bool:
    # автосгенерированные коллажи YT Music отдаются с домена каналов yt3.googleusercontent.com
    # с суффиксом =sN — Discord их часто не может прорезолвить
    return 'yt3.googleusercontent.com' in url


def _pick_playlist_thumbnail(pl_thumbs: list, tracks: list[Track]) -> str:
    pl_thumbnail = pl_thumbs[-1].get('url', '') if pl_thumbs else ''
    if not pl_thumbnail or _is_auto_yt_cover(pl_thumbnail):
        return tracks[0].thumbnail if tracks else pl_thumbnail
    return pl_thumbnail


def ytm_playlist_info(playlist_id: str, *, resolver, limit: int | None = None) -> PlaylistInfo:
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
        thumbs = item.get('thumbnails') or []
        thumbnail = thumbs[-1].get('url', '') if thumbs else ''
        tracks.append(Track(
            url=f'https://music.youtube.com/watch?v={vid}',
            title=title,
            artist=artist,
            thumbnail=thumbnail,
            resolver=resolver,
        ))
    pl_thumbnail = _pick_playlist_thumbnail(data.get('thumbnails') or [], tracks)
    return PlaylistInfo(
        tracks=tracks,
        title=data.get('title') or '',
        url=f'https://music.youtube.com/playlist?list={playlist_id}',
        thumbnail=pl_thumbnail,
        kind='playlist',
    )
