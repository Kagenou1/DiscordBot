"""Аудио-стек бота: модель трека, ffmpeg-источник и провайдеры стримов.

Публичное API:
- Track            — описание трека (title/artist/url + закэшированный extract).
- OpusAudioSource  — discord.FFmpegOpusAudio с метаданными провайдера.
- extract(url)     — превратить ссылку или поисковый запрос в Track/плейлист.
- warm_up(loop)    — прогреть кэши провайдеров на старте бота.

Диспетчер по домену ссылки: open.spotify.com -> spotify, иначе -> youtube.
"""
import asyncio
import re

from .track import Track
from .source import OpusAudioSource
from . import youtube as _youtube
from . import spotify as _spotify


YTDLSource = OpusAudioSource


_SPOTIFY_RE = re.compile(r'open\.spotify\.com/')


async def extract(url, *, loop=None, timeout=30):
    if isinstance(url, str) and _SPOTIFY_RE.search(url):
        return await _spotify.extract(url, loop=loop, timeout=timeout)
    return await _youtube.extract(url, loop=loop, timeout=timeout)


async def warm_up(loop):
    await asyncio.gather(_youtube.warm_up(loop), _spotify.warm_up(loop))


__all__ = ['Track', 'OpusAudioSource', 'YTDLSource', 'extract', 'warm_up']
