"""Прогрев Spotify-клиента на старте бота."""
import logging
import time

from .client import sp


_log = logging.getLogger('audio').info


async def warm_up(loop) -> None:
    """Чтобы первый /play не платил за получение OAuth-токена."""
    if sp is None:
        return
    t0 = time.perf_counter()
    try:
        await loop.run_in_executor(
            None, lambda: sp.search(q='test', type='track', limit=1)
        )
    except Exception as exc:
        _log(f'spotify pre-warm failed: {exc!r}')
        return
    _log(f'spotify warm-up done in {(time.perf_counter() - t0) * 1000:.0f} ms')
