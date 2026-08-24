"""Свежий стрим-URL для известного Track"""
import asyncio
import logging
import time

from ..source import OpusAudioSource
from ..track import Track
from .client import extract_info, ytm
from .search import ytm_catalog_lookup


_log = logging.getLogger('audio').info


async def _ytdl_data(url: str, *, loop, timeout: int) -> dict:
    t0 = time.perf_counter()
    data = await asyncio.wait_for(
        loop.run_in_executor(None, lambda: extract_info(url)),
        timeout=timeout,
    )
    dt = (time.perf_counter() - t0) * 1000
    if data is None:
        _log(f'extract_info {url[-12:]} unavailable in {dt:.0f} ms')
        raise RuntimeError('Видео недоступно.')
    if 'entries' in data:
        data = next((e for e in data['entries'] if e), None)
        if data is None:
            raise RuntimeError('В плейлисте нет доступных треков.')
    _log(f'extract_info {url[-12:]} {dt:.0f} ms')
    return data


async def resolve_data(track: Track, *, loop=None, timeout: int = 30) -> dict:
    """Данные потока без поднятого ffmpeg — этим пользуется заготовка"""
    loop = loop or asyncio.get_running_loop()
    try:
        return await _ytdl_data(track.url, loop=loop, timeout=timeout)
    except Exception as exc:
        if track._fallback_tried or ytm is None:
            raise
        track._fallback_tried = True
        vid = await loop.run_in_executor(
            None, lambda: ytm_catalog_lookup(track.title, track.artist, track.duration)
        )
        if not vid:
            raise
        print(f'ytmusic fallback for {track.title!r}: {vid} (was {exc!r})')
        track.url = f'https://music.youtube.com/watch?v={vid}'
        return await _ytdl_data(track.url, loop=loop, timeout=timeout)


async def resolve(track: Track, *, loop=None, timeout: int = 30) -> OpusAudioSource:
    """Resolver, привязывается к Track при создании"""
    return OpusAudioSource.from_resolved(
        await resolve_data(track, loop=loop, timeout=timeout))

