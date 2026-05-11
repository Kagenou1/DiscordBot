"""Главный извлекатель Yandex Music: ссылка -> Track или список Track."""
import asyncio
import logging
import time

from .client import yandex_client
from .parse import (
    YA_ALBUM_RE,
    YA_PLAYLIST_RE,
    YA_PLAYLIST_UUID_RE,
    YA_TRACK_RE,
    ya_track_to_track,
)
from .playlist import (
    yandex_album_tracks,
    yandex_playlist_by_uuid,
    yandex_playlist_tracks,
)
from .resolve import resolve


_log = logging.getLogger('audio').info


async def extract(url: str, *, loop=None, timeout: int = 30):
    """Возвращает ('track', Track) или ('playlist', list[Track])."""
    loop = loop or asyncio.get_running_loop()
    t_total = time.perf_counter()

    if yandex_client is None:
        raise RuntimeError('Yandex Music клиент не инициализирован.')

    track_match = YA_TRACK_RE.search(url)
    if track_match:
        track_id = track_match.group(1)
        ya_tracks = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: yandex_client.tracks([track_id])),
            timeout=timeout,
        )
        if not ya_tracks:
            raise RuntimeError('Трек Yandex Music недоступен.')
        track = ya_track_to_track(ya_tracks[0], resolver=resolve)
        if track is None:
            raise RuntimeError('Трек Yandex Music недоступен.')
        _log(f'extract -> yandex track in {(time.perf_counter() - t_total) * 1000:.0f} ms')
        return 'track', track

    uuid_match = YA_PLAYLIST_UUID_RE.search(url)
    if uuid_match:
        playlist_uuid = uuid_match.group(1)
        tracks = await asyncio.wait_for(
            loop.run_in_executor(
                None, lambda: yandex_playlist_by_uuid(playlist_uuid, resolver=resolve)
            ),
            timeout=timeout,
        )
        if not tracks:
            raise RuntimeError('Плейлист Yandex Music пуст или недоступен.')
        _log(f'extract -> yandex playlist({playlist_uuid[:8]}…) ({len(tracks)} tracks) in {(time.perf_counter() - t_total) * 1000:.0f} ms')
        return 'playlist', tracks

    playlist_match = YA_PLAYLIST_RE.search(url)
    if playlist_match:
        user_id = playlist_match.group(1)
        kind = playlist_match.group(2)
        tracks = await asyncio.wait_for(
            loop.run_in_executor(
                None, lambda: yandex_playlist_tracks(kind, user_id, resolver=resolve)
            ),
            timeout=timeout,
        )
        if not tracks:
            raise RuntimeError('Плейлист Yandex Music пуст или недоступен.')
        _log(f'extract -> yandex playlist ({len(tracks)} tracks) in {(time.perf_counter() - t_total) * 1000:.0f} ms')
        return 'playlist', tracks

    album_match = YA_ALBUM_RE.search(url)
    if album_match:
        album_id = album_match.group(1)
        tracks = await asyncio.wait_for(
            loop.run_in_executor(
                None, lambda: yandex_album_tracks(album_id, resolver=resolve)
            ),
            timeout=timeout,
        )
        if not tracks:
            raise RuntimeError('Альбом Yandex Music пуст или недоступен.')
        _log(f'extract -> yandex album ({len(tracks)} tracks) in {(time.perf_counter() - t_total) * 1000:.0f} ms')
        return 'playlist', tracks

    raise RuntimeError('Это не похоже на ссылку Yandex Music.')
