"""Прогрев Yandex Music клиента на старте"""
import logging
import time

from .client import yandex_client


_log = logging.getLogger('audio').info


async def warm_up(loop) -> None:
    """Проверка токена и прогрев соединения"""
    if yandex_client is None:
        return
    t0 = time.perf_counter()
    try:
        await loop.run_in_executor(None, lambda: yandex_client.account_status())
    except Exception as exc:
        _log(f'yandex pre-warm failed: {exc!r}')
        return
    _log(f'yandex warm-up done in {(time.perf_counter() - t0) * 1000:.0f} ms')
