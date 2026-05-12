"""Виджеты Discord-бота: эмбеды и UI-вью.

Здесь живёт всё, что бот рисует пользователю. Логика воспроизведения и
очередей — в cogs/music.py; этот пакет про оформление.
"""
from .format import (
    PROGRESS_BAR_WIDTH,
    format_time,
    format_track_label,
    progress_bar,
)
from .now_playing import PROGRESS_TICK_SECONDS, build_now_playing_embed
from .queue import QUEUE_PAGE_SIZE, QueueView


__all__ = [
    'format_time',
    'format_track_label',
    'progress_bar',
    'PROGRESS_BAR_WIDTH',
    'PROGRESS_TICK_SECONDS',
    'build_now_playing_embed',
    'QUEUE_PAGE_SIZE',
    'QueueView',
]
