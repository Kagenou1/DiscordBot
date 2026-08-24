"""Spotify-провайдер: метаданные через Web API, стрим через YouTube

- extract(url)   — точка входа: трек, альбом или плейлист
- resolve(track) — свежий стрим-URL для известного Track
- warm_up(loop)  — прогрев клиента и токена
"""
from .extract import extract
from .resolve import resolve
from .warmup import warm_up


__all__ = ['extract', 'resolve', 'warm_up']
