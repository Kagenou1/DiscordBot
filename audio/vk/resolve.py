"""Свежий стрим-URL VK Музыки через yt-dlp

Ссылка живёт минуты и привязана к адресу запросившего, поэтому берётся заново
на каждое воспроизведение, а не кэшируется из extract
"""
import asyncio
import logging
import time

from ..source import OpusAudioSource
from ..track import Track
from . import client


_log = logging.getLogger('audio').info


async def resolve_data(track: Track, *, loop=None, timeout: int = 30) -> dict:
    """Данные потока без поднятого ffmpeg — этим пользуется заготовка"""
    ytdl = client.vk_ytdl
    if ytdl is None:
        raise RuntimeError('Провайдер VK Музыки выключен.')
    loop = loop or asyncio.get_running_loop()
    t0 = time.perf_counter()
    data = await asyncio.wait_for(
        loop.run_in_executor(None, lambda: ytdl.extract_info(track.url, download=False)),
        timeout=timeout,
    )
    dt = (time.perf_counter() - t0) * 1000
    if data is None:
        _log(f'vk extract_info {track.url[-18:]} unavailable in {dt:.0f} ms')
        raise RuntimeError('Трек VK недоступен.')
    if 'entries' in data:
        data = next((e for e in data['entries'] if e), None)
        if data is None:
            raise RuntimeError('В плейлисте VK нет доступных треков.')
    _log(f'vk resolve {track.url[-18:]} in {dt:.0f} ms')
    # оба флага кладём в данные: перемотка пересоберёт источник из них же
    data['hls'] = True
    if client.vk_proxy:
        data['http_proxy'] = client.vk_proxy
    return data


async def resolve(track: Track, *, loop=None, timeout: int = 30) -> OpusAudioSource:
    """Resolver, привязывается к Track при создании"""
    return OpusAudioSource.from_resolved(
        await resolve_data(track, loop=loop, timeout=timeout))

