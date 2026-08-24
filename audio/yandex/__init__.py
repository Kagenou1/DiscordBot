"""Yandex Music провайдер: прямой стрим через yandex_music

- extract(url)   — точка входа: трек, альбом или плейлист
- resolve(track) — свежий стрим-URL в максимальном качестве
- warm_up(loop)  — прогрев клиента и проверка токена
"""
from .extract import extract
from .resolve import resolve
from .warmup import warm_up


__all__ = ['extract', 'resolve', 'warm_up']
