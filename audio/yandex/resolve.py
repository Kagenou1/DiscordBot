"""Прямой стрим-URL Yandex Music в максимальном качестве

Ранжирование: приоритет кодека flac > mp3 > aac > he-aac, внутри кодека —
максимальный bitrate. Plus-аккаунтам API отдаёт 320 kbps mp3 и иногда flac,
обычным — до 192 kbps mp3
"""
import asyncio
import logging
import time

from ..source import OpusAudioSource
from ..track import Track
from .client import yandex_client
from .parse import YA_TRACK_RE, cover_url


_log = logging.getLogger('audio').info


_CODEC_PRIORITY = {'flac': 3, 'mp3': 2, 'aac': 1, 'he-aac': 0}


def _rank(di) -> tuple:
    codec = (di.codec or '').lower()
    return (_CODEC_PRIORITY.get(codec, -1), di.bitrate_in_kbps or 0)


def _fetch_stream(track_id: str) -> dict:
    if yandex_client is None:
        raise RuntimeError('Yandex Music клиент недоступен.')
    tracks = yandex_client.tracks([track_id])
    if not tracks:
        raise RuntimeError(f'Трек Yandex Music {track_id} не найден.')
    ya_track = tracks[0]
    if not getattr(ya_track, 'available', True):
        raise RuntimeError(f'Трек Yandex Music недоступен: {ya_track.title!r}.')
    download_infos = ya_track.get_download_info(get_direct_links=False)
    if not download_infos:
        raise RuntimeError('Нет доступных вариантов скачивания.')
    best = max(download_infos, key=_rank)
    direct_url = best.get_direct_link()
    duration = (ya_track.duration_ms or 0) / 1000.0
    thumbnail = cover_url(ya_track.cover_uri)
    return {
        'url': direct_url,
        'title': ya_track.title,
        'duration': duration,
        'thumbnail': thumbnail,
        'thumbnails': [{'url': thumbnail}] if thumbnail else [],
        'acodec': best.codec,
        'abr': best.bitrate_in_kbps,
    }


async def resolve_data(track: Track, *, loop=None, timeout: int = 30) -> dict:
    """Данные потока без поднятого ffmpeg — этим пользуется заготовка"""
    loop = loop or asyncio.get_running_loop()
    m = YA_TRACK_RE.search(track.url)
    if not m:
        raise RuntimeError(f'Не удалось распарсить trackId из {track.url!r}.')
    track_id = m.group(1)

    t0 = time.perf_counter()
    data = await asyncio.wait_for(
        loop.run_in_executor(None, lambda: _fetch_stream(track_id)),
        timeout=timeout,
    )
    _log(
        f'yandex resolve {track_id}: {data["acodec"]} {data["abr"]} kbps '
        f'in {(time.perf_counter() - t0) * 1000:.0f} ms'
    )
    return data


async def resolve(track: Track, *, loop=None, timeout: int = 30) -> OpusAudioSource:
    """Resolver для Yandex-Track: прямой стрим в максимальном качестве"""
    return OpusAudioSource.from_resolved(
        await resolve_data(track, loop=loop, timeout=timeout))

