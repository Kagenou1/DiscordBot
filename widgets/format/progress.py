"""Прогресс-бар вида ▰▰▰▱▱▱ с подписью времени"""
from .time import format_time


PROGRESS_BAR_WIDTH = 22


def progress_bar(elapsed: float, total: float, *, width: int = PROGRESS_BAR_WIDTH) -> str:
    if total <= 0:
        return f'`{"▱" * width}` {format_time(elapsed)}'
    display_elapsed = min(elapsed, total)
    pct = max(0.0, display_elapsed / total)
    filled = int(round(pct * width))
    bar = '▰' * filled + '▱' * (width - filled)
    return f'`{bar}` {format_time(display_elapsed)} / {format_time(total)}'
