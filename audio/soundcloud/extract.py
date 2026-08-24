"""Извлекатель SoundCloud: ссылка -> Track либо PlaylistInfo"""
import asyncio
import logging
import time

from ..track import PlaylistInfo, Track
from .client import sc_ytdl
from .parse import SC_SET_RE, entry_to_track
from .resolve import resolve


_log = logging.getLogger('audio').info


async def extract(url: str, *, loop=None, timeout: int = 30):
    """('track', Track) либо ('playlist', PlaylistInfo)"""
    loop = loop or asyncio.get_running_loop()
    t_total = time.perf_counter()

    t0 = time.perf_counter()
    data = await asyncio.wait_for(
        loop.run_in_executor(None, lambda: sc_ytdl.extract_info(url, download=False)),
        timeout=timeout,
    )
    _log(f'soundcloud extract_info: {(time.perf_counter() - t0) * 1000:.0f} ms')
    if data is None:
        raise RuntimeError('Ничего не найдено или ресурс SoundCloud недоступен.')

    is_set = SC_SET_RE.search(url) is not None or 'entries' in data
    if is_set and 'entries' in data:
        tracks = [t for t in (entry_to_track(e, resolver=resolve) for e in data['entries']) if t]
        if not tracks:
            raise RuntimeError('Сет SoundCloud пуст или недоступен.')
        pl_thumbs = data.get('thumbnails') or []
        pl_thumbnail = pl_thumbs[-1].get('url', '') if pl_thumbs else (tracks[0].thumbnail if tracks else '')
        info = PlaylistInfo(
            tracks=tracks,
            title=data.get('title') or '',
            url=data.get('webpage_url') or url,
            thumbnail=pl_thumbnail,
            kind='playlist',
        )
        _log(f'extract -> soundcloud set ({len(tracks)} tracks) in {(time.perf_counter() - t_total) * 1000:.0f} ms')
        return 'playlist', info

    track = entry_to_track(data, resolver=resolve) or Track(
        url=data.get('webpage_url') or url,
        title=data.get('title') or 'Без названия',
        resolver=resolve,
    )
    if data.get('url'):
        track.cache_resolved(data)
    _log(f'extract -> soundcloud track in {(time.perf_counter() - t_total) * 1000:.0f} ms')
    return 'track', track
