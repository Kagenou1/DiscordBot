"""Виджеты бота: эмбеды и UI-вью

Всё, что бот рисует пользователю. Логика воспроизведения и очередей в cogs/music.py
"""
from .added import build_added_playlist_embed, build_added_track_embed
from .format import (
    PROGRESS_BAR_WIDTH,
    format_time,
    format_track_label,
    plain_error,
    progress_bar,
    set_emojis,
    source_label,
    source_line,
)
from .now_playing import PROGRESS_TICK_SECONDS, build_current_track_embed, build_now_playing_embed
from .queue import QUEUE_PAGE_SIZE, QueueView


__all__ = [
    'build_added_playlist_embed',
    'build_added_track_embed',
    'build_current_track_embed',
    'format_time',
    'format_track_label',
    'progress_bar',
    'PROGRESS_BAR_WIDTH',
    'PROGRESS_TICK_SECONDS',
    'build_now_playing_embed',
    'QUEUE_PAGE_SIZE',
    'QueueView',
    'set_emojis',
    'source_label',
    'source_line',
    'plain_error',
]
