"""Сборка эмбеда «Сейчас играет»."""
import discord

from audio import OpusAudioSource, Track

from ..format import PROGRESS_BAR_WIDTH, progress_bar
from .thumbnail import pick_thumbnail


PROGRESS_TICK_SECONDS = 8


_STATE_EMOJI = {
    'playing': '▶️',
    'paused': '⏸️',
    'stopped': '⏹️',
}


def build_now_playing_embed(
    track: Track,
    source: OpusAudioSource,
    elapsed: float,
    *,
    state: str = 'playing',
) -> discord.Embed:
    duration = float(source.data.get('duration') or 0)
    # track.thumbnail курируется на этапе extract: для YT Music / Yandex / Spotify / SC
    # это квадратная обложка альбома, а у yt-dlp в source.data — 16:9 видео-кадр.
    thumbnail = track.thumbnail or pick_thumbnail(source.data)
    title = track.title or 'Без названия'
    emoji = _STATE_EMOJI.get(state, _STATE_EMOJI['playing'])

    lines: list[str] = [f'[**{title}**]({track.url})' if track.url else f'**{title}**']
    lines.append('\u200B')
    if track.artist:
        lines.append(track.artist)
        lines.append('\u200B')
    lines.append(f'{emoji} {progress_bar(elapsed, duration, width=PROGRESS_BAR_WIDTH)}')

    embed = discord.Embed(
        description='\n'.join(lines),
        color=discord.Color.blurple(),
    )
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    return embed
