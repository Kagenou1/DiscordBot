"""Стрим-URL для Spotify-трека через поиск на YouTube

Web API не отдаёт аудио-поток, поэтому resolve = найти эквивалент
на YouTube Music и построить OpusAudioSource через yt-dlp
"""
import asyncio
import logging
import time

from ..source import OpusAudioSource
from ..track import Track
from ..youtube.client import extract_info
from ..youtube.search import ytm_catalog_lookup


_log = logging.getLogger('audio').info


async def resolve_data(track: Track, *, loop=None, timeout: int = 30) -> dict:
    """Данные потока без поднятого ffmpeg — этим пользуется заготовка"""
    loop = loop or asyncio.get_running_loop()

    t0 = time.perf_counter()
    vid = await loop.run_in_executor(
        None, lambda: ytm_catalog_lookup(track.title, track.artist, track.duration)
    )
    if not vid:
        raise RuntimeError(f'YouTube-эквивалент для {track.title!r} не найден.')
    yt_url = f'https://music.youtube.com/watch?v={vid}'
    _log(f'spotify->yt {track.title!r}: {vid} in {(time.perf_counter() - t0) * 1000:.0f} ms')

    t1 = time.perf_counter()
    data = await asyncio.wait_for(
        loop.run_in_executor(None, lambda: extract_info(yt_url)),
        timeout=timeout,
    )
    dt = (time.perf_counter() - t1) * 1000
    if data is None:
        _log(f'extract_info {yt_url[-12:]} unavailable in {dt:.0f} ms')
        raise RuntimeError('Видео недоступно.')
    if 'entries' in data:
        data = next((e for e in data['entries'] if e), None)
        if data is None:
            raise RuntimeError('В плейлисте нет доступных треков.')
    _log(f'extract_info {yt_url[-12:]} {dt:.0f} ms')
    return data


async def resolve(track: Track, *, loop=None, timeout: int = 30) -> OpusAudioSource:
    """Resolver для Spotify-Track: поиск по title и artist на YT Music"""
    return OpusAudioSource.from_resolved(
        await resolve_data(track, loop=loop, timeout=timeout))

