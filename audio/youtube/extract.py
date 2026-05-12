"""Главный извлекатель: ссылка/поиск -> Track или список Track."""
import asyncio
import logging
import time

from ..track import Track
from .client import ytdl, ytm
from .parse import YTM_PLAYLIST_RE, entry_to_track
from .playlist import ytm_playlist_tracks
from .resolve import resolve


_log = logging.getLogger('audio').info


async def extract(url: str, *, loop=None, timeout: int = 30):
    """Возвращает ('track', Track) или ('playlist', list[Track])."""
    loop = loop or asyncio.get_running_loop()
    t_total = time.perf_counter()

    ytm_match = YTM_PLAYLIST_RE.search(url)
    if ytm_match and ytm is not None:
        playlist_id = ytm_match.group(1)
        try:
            t0 = time.perf_counter()
            tracks = await asyncio.wait_for(
                loop.run_in_executor(
                    None, lambda: ytm_playlist_tracks(playlist_id, resolver=resolve, limit=None)
                ),
                timeout=timeout,
            )
            _log(f'ytm playlist: {len(tracks)} tracks in {(time.perf_counter() - t0) * 1000:.0f} ms')
            if tracks:
                _log(f'extract -> playlist in {(time.perf_counter() - t_total) * 1000:.0f} ms')
                return 'playlist', tracks
        except Exception as exc:
            print(f'ytmusic playlist fast-path failed for {playlist_id}: {exc!r} — falling back to yt-dlp')

    t0 = time.perf_counter()
    data = await asyncio.wait_for(
        loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=False)),
        timeout=timeout,
    )
    _log(f'ytdl extract_info (extract): {(time.perf_counter() - t0) * 1000:.0f} ms')
    if data is None:
        raise RuntimeError('Ничего не найдено или ресурс недоступен.')

    if 'entries' in data:
        tracks = [t for t in (entry_to_track(e, resolver=resolve) for e in data['entries']) if t]
        if not tracks:
            raise RuntimeError('Плейлист пуст или недоступен.')
        _log(f'extract -> playlist ({len(tracks)} tracks) in {(time.perf_counter() - t_total) * 1000:.0f} ms')
        return 'playlist', tracks

    track = entry_to_track(data, resolver=resolve) or Track(
        url=data.get('webpage_url') or url,
        title=data.get('title') or 'Без названия',
        resolver=resolve,
    )
    if data.get('url'):
        track.cache_resolved(data)
    _log(f'extract -> track in {(time.perf_counter() - t_total) * 1000:.0f} ms')
    return 'track', track
