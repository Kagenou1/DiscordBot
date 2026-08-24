"""Сборка эмбеда «Сейчас играет»"""
import discord

from audio import OpusAudioSource, Track

from ..format import PROGRESS_BAR_WIDTH, progress_bar, source_line
from .thumbnail import pick_thumbnail


PROGRESS_TICK_SECONDS = 8


_STATE_EMOJI = {
    'playing': '▶️',
    'paused': '⏸️',
    'stopped': '⏹️',
}

# Юникод, а не эмодзи приложения: кастомные (<:name:id>) в футере эмбеда
# не рендерятся вовсе, а в описании заняли бы место прогресс-бара
_REPEAT_EMOJI = {
    'track': '🔂',
    'queue': '🔁',
}


def build_now_playing_embed(
    track: Track,
    source: OpusAudioSource,
    elapsed: float,
    *,
    state: str = 'playing',
    repeat: str = 'off',
) -> discord.Embed:
    duration = float(source.data.get('duration') or 0)
    # track.thumbnail проставляется на extract: квадратная обложка альбома,
    # тогда как в source.data у yt-dlp лежит 16:9 кадр
    thumbnail = track.thumbnail or pick_thumbnail(source.data)
    title = track.title or 'Без названия'
    emoji = _STATE_EMOJI.get(state, _STATE_EMOJI['playing'])

    title_line = f'## [{title}]({track.url})' if track.url else f'## {title}'
    lines: list[str] = [title_line]
    if track.artist:
        lines.append(track.artist)
    # выключенный повтор не показываем: строка и так плотная
    mark = _REPEAT_EMOJI.get(repeat, '')
    bar = progress_bar(elapsed, duration, width=PROGRESS_BAR_WIDTH)
    lines.append(f'{emoji} {bar}{f"  {mark}" if mark else ""}')
    source = source_line(track.url)
    if source:
        lines += ['', source]   # пустая строка отделяет служебное от трека

    embed = discord.Embed(
        description='\n'.join(lines),
        color=discord.Color.blurple(),
    )
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    return embed


def build_current_track_embed(track: Track, source: OpusAudioSource) -> discord.Embed:
    """Снапшот текущего трека без прогресс-бара и обновлений"""
    thumbnail = track.thumbnail or pick_thumbnail(source.data)
    title = track.title or 'Без названия'
    title_line = f'## [{title}]({track.url})' if track.url else f'## {title}'
    lines: list[str] = [title_line]
    if track.artist:
        lines.append(track.artist)
    source = source_line(track.url)
    if source:
        lines += ['', source]
    embed = discord.Embed(
        description='\n'.join(lines),
        color=discord.Color.blurple(),
    )
    embed.set_author(name='Сейчас играет')
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    return embed
