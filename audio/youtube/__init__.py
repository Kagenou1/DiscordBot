"""YouTube-провайдер: yt-dlp + ytmusicapi.

Публичные функции:
- extract(url)   — единственная точка входа: трек/плейлист/поиск.
- resolve(track) — добывает свежий стрим-URL для уже известного Track.
- warm_up(loop)  — прогрев yt-dlp и ytmusicapi.
"""
from .extract import extract
from .resolve import resolve
from .warmup import warm_up


__all__ = ['extract', 'resolve', 'warm_up']
