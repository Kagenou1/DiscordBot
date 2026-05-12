"""Секунды -> 'M:SS' или 'H:MM:SS'."""


def format_time(seconds: float) -> str:
    s = max(0, int(seconds))
    if s >= 3600:
        return f'{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}'
    return f'{s // 60}:{s % 60:02d}'
