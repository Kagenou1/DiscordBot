"""Сборка эмбеда «Сейчас играет»."""
import discord

from audio import OpusAudioSource, Track

from ..format import PROGRESS_BAR_WIDTH, progress_bar
from .thumbnail import pick_thumbnail


PROGRESS_TICK_SECONDS = 8


def build_now_playing_embed(
    track: Track, source: OpusAudioSource, elapsed: float
) -> discord.Embed:
    duration = float(source.data.get('duration') or 0)
    thumbnail = pick_thumbnail(source.data)

    parts: list[str] = []
    if track.artist:
        parts.append(f'**{track.artist}**')
        parts.append('\u200B')
    parts.append(progress_bar(elapsed, duration, width=PROGRESS_BAR_WIDTH))

    embed = discord.Embed(
        title=track.title or 'Без названия',
        url=track.url or None,
        description='\n'.join(parts),
        color=discord.Color.blurple(),
    )
    embed.set_author(name='Сейчас играет')
    if thumbnail:
        embed.set_image(url=thumbnail)
    return embed
