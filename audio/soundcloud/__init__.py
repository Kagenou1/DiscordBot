"""SoundCloud-провайдер: прямой стрим через yt-dlp

- extract(url)   — точка входа: трек или сет
- resolve(track) — свежий стрим-URL для известного Track
- warm_up(loop)  — заглушка, отдельный клиент не нужен
"""
from .extract import extract
from .resolve import resolve
from .warmup import warm_up


__all__ = ['extract', 'resolve', 'warm_up']
