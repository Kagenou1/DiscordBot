"""Свежий стрим-URL SoundCloud через yt-dlp"""
import asyncio
import logging
import time

from ..source import OpusAudioSource
from ..track import Track
from .client import sc_ytdl


_log = logging.getLogger('audio').info


async def resolve_data(track: Track, *, loop=None, timeout: int = 30) -> dict:
    """Данные потока без поднятого ffmpeg — этим пользуется заготовка"""
    loop = loop or asyncio.get_running_loop()
    t0 = time.perf_counter()
    data = await asyncio.wait_for(
        loop.run_in_executor(None, lambda: sc_ytdl.extract_info(track.url, download=False)),
        timeout=timeout,
    )
    dt = (time.perf_counter() - t0) * 1000
    if data is None:
        _log(f'soundcloud extract_info {track.url[-16:]} unavailable in {dt:.0f} ms')
        raise RuntimeError('Трек SoundCloud недоступен.')
    if 'entries' in data:
        data = next((e for e in data['entries'] if e), None)
        if data is None:
            raise RuntimeError('В сете SoundCloud нет доступных треков.')
    if 'preview' in (data.get('format_id') or '').lower() or '/preview/' in (data.get('url') or ''):
        raise RuntimeError('Трек SoundCloud доступен только в виде превью (SC Go+).')
    _log(f'soundcloud extract_info {track.url[-16:]} {dt:.0f} ms')
    return data


async def resolve(track: Track, *, loop=None, timeout: int = 30) -> OpusAudioSource:
    """Resolver, привязывается к Track при создании"""
    return OpusAudioSource.from_resolved(
        await resolve_data(track, loop=loop, timeout=timeout))

