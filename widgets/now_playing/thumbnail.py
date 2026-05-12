"""Выбор обложки из метаданных yt-dlp."""


def pick_thumbnail(data: dict) -> str | None:
    thumb = data.get('thumbnail')
    if thumb:
        return thumb
    thumbs = data.get('thumbnails') or []
    if thumbs:
        return thumbs[-1].get('url')
    return None
