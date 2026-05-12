"""Поиск videoId через структурированный каталог YT Music."""
from .client import ytm


def ytm_catalog_lookup(title: str, artist: str = '') -> str | None:
    """Подобрать playable videoId по названию/артисту через YT Music."""
    if ytm is None or not title:
        return None
    query = f'{artist} {title}'.strip()
    try:
        results = ytm.search(query, filter='songs', limit=5)
    except Exception as exc:
        print(f'ytmusic search error: {exc!r}')
        return None
    for item in results or []:
        vid = item.get('videoId')
        if vid:
            return vid
    return None
