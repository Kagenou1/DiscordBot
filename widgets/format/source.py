"""Метка источника по URL: YouTube, Spotify, Yandex, SoundCloud, VK

Иконки — эмодзи приложения, а не сервера: они принадлежат боту и работают
в любой гильдии, где он есть, не занимая серверных слотов. Заполняются на
старте из `fetch_application_emojis`, загружает их `tools/upload_emoji.py`.

Строка источника живёт в описании эмбеда, а не в футере: кастомные эмодзи
(`<:name:id>`) футер показывает сырым текстом, юникодные — рисует. Отсюда
и перенос: иначе вместо иконки стояло бы «<:spotify:123>»
"""


_RULES: tuple[tuple[str, str], ...] = (
    ('music.youtube.com', 'YouTube Music'),
    ('youtube.com', 'YouTube'),
    ('youtu.be', 'YouTube'),
    ('open.spotify.com', 'Spotify'),
    ('music.yandex.', 'Yandex Music'),
    ('soundcloud.com', 'SoundCloud'),
    # только аудио-ссылки: под vk.ru/vk.com попадают и профили, и видео
    ('vk.ru/audio', 'VK Музыка'),
    ('vk.com/audio', 'VK Музыка'),
    ('vk.ru/music/', 'VK Музыка'),
    ('vk.com/music/', 'VK Музыка'),
)

# метка -> имя эмодзи приложения; имя Discord принимает только из букв,
# цифр и подчёркиваний
EMOJI_NAMES: dict[str, str] = {
    'YouTube Music': 'youtube_music',
    'YouTube': 'youtube',
    'Spotify': 'spotify',
    'Yandex Music': 'yandex_music',
    'SoundCloud': 'soundcloud',
    'VK Музыка': 'vk',
}

# имя эмодзи -> разметка <:name:id>, заполняется на прогреве
_MARKUP: dict[str, str] = {}


def set_emojis(markup: dict) -> None:
    """Запомнить разметку эмодзи по именам

    Нет эмодзи — не беда: строка источника останется текстовой. Так бот
    работает и до первой загрузки, и если у сервиса логотипа нет вовсе
    """
    _MARKUP.clear()
    _MARKUP.update({str(k): str(v) for k, v in (markup or {}).items()})


def source_label(url: str) -> str:
    if not url:
        return ''
    for needle, label in _RULES:
        if needle in url:
            return label
    return ''


def source_emoji(url: str) -> str:
    """Разметка иконки сервиса, либо пусто"""
    return _MARKUP.get(EMOJI_NAMES.get(source_label(url), ''), '')


def source_line(url: str) -> str:
    """Строка «Источник» для описания эмбеда, либо пусто

    Без `-#`: подтекст Discord рисует приглушённым, и строка отличалась
    цветом от остального описания. Иконка идёт хвостом, текст остаётся
    на месте и без неё
    """
    label = source_label(url)
    if not label:
        return ''
    emoji = source_emoji(url)
    return f'Источник: {label}{f" {emoji}" if emoji else ""}'
