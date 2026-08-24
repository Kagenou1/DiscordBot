"""Извлекатель Spotify: ссылка -> Track либо PlaylistInfo"""
import asyncio
import logging
import time

from .client import sp
from .parse import (
    SPOTIFY_ALBUM_RE,
    SPOTIFY_PLAYLIST_RE,
    SPOTIFY_TRACK_RE,
    item_to_track,
)
from .playlist import spotify_album_info, spotify_playlist_info
from .resolve import resolve


_log = logging.getLogger('audio').info


async def extract(url: str, *, loop=None, timeout: int = 30):
    """('track', Track) либо ('playlist', PlaylistInfo)"""
    loop = loop or asyncio.get_running_loop()
    t_total = time.perf_counter()

    if sp is None:
        raise RuntimeError('Spotify-клиент не инициализирован.')

    track_match = SPOTIFY_TRACK_RE.search(url)
    if track_match:
        track_id = track_match.group(1)
        item = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: sp.track(track_id)),
            timeout=timeout,
        )
        track = item_to_track(item, resolver=resolve)
        if track is None:
            raise RuntimeError('Трек Spotify недоступен.')
        _log(f'extract -> spotify track in {(time.perf_counter() - t_total) * 1000:.0f} ms')
        return 'track', track

    album_match = SPOTIFY_ALBUM_RE.search(url)
    if album_match:
        album_id = album_match.group(1)
        info = await asyncio.wait_for(
            spotify_album_info(album_id, resolver=resolve, loop=loop),
            timeout=timeout,
        )
        if not info.tracks:
            raise RuntimeError('Альбом Spotify пуст или недоступен.')
        _log(f'extract -> spotify album ({len(info.tracks)} tracks) in {(time.perf_counter() - t_total) * 1000:.0f} ms')
        return 'playlist', info

    playlist_match = SPOTIFY_PLAYLIST_RE.search(url)
    if playlist_match:
        playlist_id = playlist_match.group(1)
        info = await asyncio.wait_for(
            spotify_playlist_info(playlist_id, resolver=resolve, loop=loop),
            timeout=timeout,
        )
        if not info.tracks:
            raise RuntimeError('Плейлист Spotify пуст или недоступен.')
        _log(f'extract -> spotify playlist ({len(info.tracks)} tracks) in {(time.perf_counter() - t_total) * 1000:.0f} ms')
        return 'playlist', info

    raise RuntimeError('Это не похоже на ссылку Spotify.')
