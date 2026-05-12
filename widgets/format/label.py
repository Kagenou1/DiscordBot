"""Подпись трека: 'Title — Artist' или просто 'Title'."""
from audio import Track


def format_track_label(track: Track) -> str:
    return f'{track.title} — {track.artist}' if track.artist else track.title
