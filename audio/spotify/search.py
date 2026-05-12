"""Поиск трека на Spotify по названию/артисту (резерв на будущее)."""
from .client import sp


def spotify_lookup(title: str, artist: str = '') -> dict | None:
    """Найти первый трек на Spotify по запросу. Не используется сейчас."""
    if sp is None or not title:
        return None
    query = f'{artist} {title}'.strip()
    try:
        result = sp.search(q=query, type='track', limit=1)
    except Exception as exc:
        print(f'spotify search error: {exc!r}')
        return None
    items = ((result or {}).get('tracks') or {}).get('items') or []
    return items[0] if items else None
