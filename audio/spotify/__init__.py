"""Spotify-провайдер: метаданные через Spotify Web API, стрим — через YouTube.

Публичные функции:
- extract(url)   — единственная точка входа: трек/альбом/плейлист.
- resolve(track) — добывает свежий стрим-URL для уже известного Track.
- warm_up(loop)  — прогрев Spotify-клиента (токен).
"""
from .extract import extract
from .resolve import resolve
from .warmup import warm_up


__all__ = ['extract', 'resolve', 'warm_up']
