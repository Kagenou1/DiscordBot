"""VK Музыка: прямой стрим через yt-dlp со своим экстрактором

- extract(url)   — точка входа: трек, альбом или плейлист
- resolve(track) — свежий стрим-URL, ссылка живёт минуты
- warm_up(loop)  — маршрут, клиенты и проверка сессии

Нужны куки залогиненного аккаунта: без них VK отдаёт 31 секунду при полной
заявленной длительности и никак это не помечает
"""
from .extract import extract
from .resolve import resolve
from .warmup import warm_up


__all__ = ['extract', 'resolve', 'warm_up']
