"""Сборка эмбедов «добавлено в очередь»."""
import discord

from audio import Track
from audio.track import PlaylistInfo

from ..format import source_label


def _linked(text: str, url: str) -> str:
    return f'[{text}]({url})' if url else text


def _with_source(embed: discord.Embed, url: str) -> None:
    label = source_label(url)
    if label:
        embed.set_footer(text=f'Источник: {label}')


def build_added_track_embed(track: Track) -> discord.Embed:
    """Эмбед при добавлении одного трека в очередь."""
    title = track.title or 'Без названия'
    lines: list[str] = [f'## {_linked(title, track.url)}']
    if track.artist:
        lines.append(track.artist)
    embed = discord.Embed(
        description='\n'.join(lines),
        color=discord.Color.green(),
    )
    embed.set_author(name='Добавлено в очередь')
    if track.thumbnail:
        embed.set_thumbnail(url=track.thumbnail)
    _with_source(embed, track.url)
    return embed


def build_added_playlist_embed(info: PlaylistInfo, *, shuffled: bool = False) -> discord.Embed:
    """Эмбед при добавлении плейлиста/альбома в очередь."""
    label = 'альбом' if info.kind == 'album' else 'плейлист'
    header = f'Добавлен {label}' + (' (перемешано)' if shuffled else '')
    title = info.title or label.capitalize()
    lines: list[str] = [
        f'## {_linked(title, info.url)}',
        f'-# {len(info.tracks)} треков',
    ]
    embed = discord.Embed(
        description='\n'.join(lines),
        color=discord.Color.green(),
    )
    embed.set_author(name=header)
    if info.thumbnail:
        embed.set_thumbnail(url=info.thumbnail)
    source_url = info.url or (info.tracks[0].url if info.tracks else '')
    _with_source(embed, source_url)
    return embed
