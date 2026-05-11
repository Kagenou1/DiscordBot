"""Yandex Music провайдер: прямой стрим через yandex_music.

Публичные функции:
- extract(url)   — единственная точка входа: трек/альбом/плейлист.
- resolve(track) — добывает свежий стрим-URL с максимальным качеством.
- warm_up(loop)  — прогрев клиента (проверка токена).
"""
from .extract import extract
from .resolve import resolve
from .warmup import warm_up


__all__ = ['extract', 'resolve', 'warm_up']
