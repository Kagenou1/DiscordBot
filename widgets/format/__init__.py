"""Маленькие форматтеры для эмбедов."""
from .label import format_track_label
from .progress import PROGRESS_BAR_WIDTH, progress_bar
from .time import format_time


__all__ = [
    'format_time',
    'format_track_label',
    'progress_bar',
    'PROGRESS_BAR_WIDTH',
]
