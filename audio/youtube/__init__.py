"""YouTube-провайдер: yt-dlp и ytmusicapi

- extract(url)   — точка входа: трек, плейлист или поиск
- resolve(track) — свежий стрим-URL для известного Track
- warm_up(loop)  — прогрев yt-dlp и ytmusicapi
"""
from .extract import extract
from .resolve import resolve
from .warmup import warm_up


__all__ = ['extract', 'resolve', 'warm_up']
