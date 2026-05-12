"""Получение свежего стрим-URL для уже известного Track."""
import asyncio
import logging
import time

from ..source import OpusAudioSource
from ..track import Track
from .client import ytdl, ytm
from .search import ytm_catalog_lookup


_log = logging.getLogger('audio').info


async def _ytdl_to_source(url: str, *, loop, timeout: int) -> OpusAudioSource:
    t0 = time.perf_counter()
    data = await asyncio.wait_for(
        loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=False)),
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
    return OpusAudioSource.from_resolved(data)


async def resolve(track: Track, *, loop=None, timeout: int = 30) -> OpusAudioSource:
    """Resolver, который привязывается к Track при создании."""
    loop = loop or asyncio.get_running_loop()
    try:
        return await _ytdl_to_source(track.url, loop=loop, timeout=timeout)
    except Exception as exc:
        if track._fallback_tried or ytm is None:
            raise
        track._fallback_tried = True
        vid = await loop.run_in_executor(
            None, lambda: ytm_catalog_lookup(track.title, track.artist)
        )
        if not vid:
            raise
        print(f'ytmusic fallback for {track.title!r}: {vid} (was {exc!r})')
        track.url = f'https://music.youtube.com/watch?v={vid}'
        return await _ytdl_to_source(track.url, loop=loop, timeout=timeout)
