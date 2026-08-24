"""Аудио-стек бота: модель трека, ffmpeg-источник и провайдеры стримов

Публичное API:
- Track            — описание трека: title/artist/url и закэшированный extract
- OpusAudioSource  — discord.FFmpegOpusAudio с метаданными провайдера
- extract(url)     — ссылка или поисковый запрос -> Track или плейлист
- warm_up(loop)    — прогрев кэшей провайдеров на старте

Диспетчер по домену: open.spotify.com -> spotify, music.yandex.* -> yandex,
soundcloud.com -> soundcloud, vk.com/vk.ru -> vk,
иначе youtube, включая поисковый fallback
"""
import asyncio
import re

from .track import Track
from .source import OpusAudioSource
from .youtube.client import rotate_profile as rotate_stream_client
from . import youtube as _youtube
from . import spotify as _spotify
from . import yandex as _yandex
from . import soundcloud as _soundcloud
from . import vk as _vk


YTDLSource = OpusAudioSource


_SPOTIFY_RE = re.compile(r'open\.spotify\.com/')
_YANDEX_RE = re.compile(r'music\.yandex\.(?:ru|by|kz|com)/')
_SOUNDCLOUD_RE = re.compile(r'(?:on\.|m\.)?soundcloud\.com/')
_VK_RE = re.compile(r'(?:(?:m|new)\.)?vk\.(?:com|ru)/(?:audio|music/(?:album|playlist)/|.*[?&](?:act|z)=audio_playlist)')


async def extract(url, *, loop=None, timeout=30):
    if isinstance(url, str):
        if _SPOTIFY_RE.search(url):
            return await _spotify.extract(url, loop=loop, timeout=timeout)
        if _YANDEX_RE.search(url):
            return await _yandex.extract(url, loop=loop, timeout=timeout)
        if _SOUNDCLOUD_RE.search(url):
            return await _soundcloud.extract(url, loop=loop, timeout=timeout)
        if _VK_RE.search(url):
            return await _vk.extract(url, loop=loop, timeout=timeout)
    return await _youtube.extract(url, loop=loop, timeout=timeout)


async def warm_up(loop):
    await asyncio.gather(
        _youtube.warm_up(loop),
        _spotify.warm_up(loop),
        _yandex.warm_up(loop),
        _soundcloud.warm_up(loop),
        _vk.warm_up(loop),
    )


__all__ = ['Track', 'OpusAudioSource', 'YTDLSource', 'extract', 'warm_up',
           'rotate_stream_client']
