"""Прогрев VK Музыки: маршрут, клиенты, проверка сессии

Прокси поднимается только если задан vk_proxy. Пустой означает, что бот ходит
в VK своим обычным маршрутом — это рабочий режим, если куки выданы тому же
адресу. Сессия привязана к адресу входа, поэтому маршрут кук и маршрут бота
обязаны совпадать
"""
import logging
import time

from . import client, proxy


_log = logging.getLogger('audio').info


async def warm_up(loop) -> None:
    if not client.vk_cookies:
        return
    if client.vk_proxy:
        proxy.start(client.vk_proxy)

    t0 = time.perf_counter()
    # id читается из кэша на диске, а при первом запуске добывается запросом,
    # поэтому клиенты достраиваются здесь, после подъёма маршрута
    try:
        ok = await loop.run_in_executor(None, client.ensure)
    except Exception as exc:
        _log(f'vk warm-up failed: {exc!r}')
        return
    if not ok:
        return
    _log(f'vk warm-up done in {(time.perf_counter() - t0) * 1000:.0f} ms')
