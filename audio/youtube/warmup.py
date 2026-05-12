"""Прогрев yt-dlp/Deno/ytmusicapi на старте бота."""
import asyncio
import logging
import time

from .client import ytdl, ytm


_log = logging.getLogger('audio').info


_WARM_URL = 'https://music.youtube.com/watch?v=lYBUbBu4W08&si=8Ielbx7nNY3fsXw6'


async def warm_up(loop) -> None:
    """Чтобы первый /play не платил холодный старт инструментов."""
    t0 = time.perf_counter()

    async def warm_ytdl():
        try:
            await loop.run_in_executor(
                None, lambda: ytdl.extract_info(_WARM_URL, download=False)
            )
        except Exception as exc:
            _log(f'yt-dlp pre-warm failed: {exc!r}')

    async def warm_ytm():
        if ytm is None:
            return
        try:
            await loop.run_in_executor(None, lambda: ytm.search('test', limit=1))
        except Exception as exc:
            _log(f'ytmusicapi pre-warm failed: {exc!r}')

    await asyncio.gather(warm_ytdl(), warm_ytm())
    _log(f'youtube warm-up done in {(time.perf_counter() - t0) * 1000:.0f} ms')
