"""Метка источника по URL (YouTube/Spotify/Yandex/SoundCloud)."""


_RULES: tuple[tuple[str, str], ...] = (
    ('music.youtube.com', 'YouTube Music'),
    ('youtube.com', 'YouTube'),
    ('youtu.be', 'YouTube'),
    ('open.spotify.com', 'Spotify'),
    ('music.yandex.', 'Yandex Music'),
    ('soundcloud.com', 'SoundCloud'),
)


def source_label(url: str) -> str:
    if not url:
        return ''
    for needle, label in _RULES:
        if needle in url:
            return label
    return ''
