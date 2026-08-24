"""Извлекатель VK Музыки: ссылка -> Track либо PlaylistInfo"""
import asyncio
import logging
import time

from ..track import PlaylistInfo, Track
from . import client
from .parse import VK_PLAYLIST_RE, entry_to_track
from .resolve import resolve


_log = logging.getLogger('audio').info


async def extract(url: str, *, loop=None, timeout: int = 30):
    """('track', Track) либо ('playlist', PlaylistInfo)"""
    if client.vk_ytdl is None:
        raise RuntimeError('Провайдер VK Музыки выключен: нет кук в private.py.')
    loop = loop or asyncio.get_running_loop()
    t_total = time.perf_counter()

    is_playlist = VK_PLAYLIST_RE.search(url) is not None
    ytdl = client.vk_flat_ytdl if is_playlist else client.vk_ytdl

    data = await asyncio.wait_for(
        loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=False)),
        timeout=timeout,
    )
    if data is None:
        raise RuntimeError('Ничего не найдено или ресурс VK недоступен.')

    if 'entries' in data:
        tracks = [t for t in (entry_to_track(e, resolver=resolve)
                              for e in data['entries']) if t]
        if not tracks:
            # заблокированные правообладателем отсеиваются в экстракторе,
            # поэтому пустой список чаще означает регион, а не пустой плейлист
            raise RuntimeError(
                'В плейлисте VK нет доступных треков — возможно, все ограничены '
                'правообладателем в этом регионе.')
        thumbs = data.get('thumbnails') or []
        thumbnail = thumbs[-1].get('url', '') if thumbs else tracks[0].thumbnail
        # альбом от плейлиста в выдаче не отличается, поэтому artist не ставим:
        # у карточки он показывается только для альбомов
        info = PlaylistInfo(
            tracks=tracks,
            title=data.get('title') or '',
            url=data.get('webpage_url') or url,
            thumbnail=thumbnail,
            kind='playlist',
        )
        _log(f'extract -> vk playlist ({len(tracks)} tracks) '
             f'in {(time.perf_counter() - t_total) * 1000:.0f} ms')
        return 'playlist', info

    track = entry_to_track(data, resolver=resolve) or Track(
        url=data.get('webpage_url') or url,
        title=data.get('title') or 'Без названия',
        resolver=resolve,
    )
    # ссылка VK живёт минуты и привязана к адресу, кэшировать её из extract нельзя
    _log(f'extract -> vk track in {(time.perf_counter() - t_total) * 1000:.0f} ms')
    return 'track', track
