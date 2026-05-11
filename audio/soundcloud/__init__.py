"""SoundCloud-провайдер: прямой стрим через yt-dlp.

Публичные функции:
- extract(url)   — единственная точка входа: трек/сет (плейлист/альбом).
- resolve(track) — добывает свежий стрим-URL для уже известного Track.
- warm_up(loop)  — заглушка (отдельный клиент не нужен, yt-dlp уже греется).
"""
from .extract import extract
from .resolve import resolve
from .warmup import warm_up


__all__ = ['extract', 'resolve', 'warm_up']
