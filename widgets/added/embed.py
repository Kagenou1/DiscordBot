"""Сборка эмбедов «добавлено в очередь»."""
import discord

from audio import Track
from audio.track import PlaylistInfo


def _linked(text: str, url: str) -> str:
    return f'[{text}]({url})' if url else text


def build_added_track_embed(track: Track) -> discord.Embed:
    """Эмбед при добавлении одного трека в очередь."""
    description = _linked(f'**{track.title}**', track.url)
    if track.artist:
        description += f'\n{track.artist}'
    embed = discord.Embed(
        description=description,
        color=discord.Color.green(),
    )
    embed.set_author(name='Добавлено в очередь')
    if track.thumbnail:
        embed.set_thumbnail(url=track.thumbnail)
    return embed


def build_added_playlist_embed(info: PlaylistInfo, *, shuffled: bool = False) -> discord.Embed:
    """Эмбед при добавлении плейлиста/альбома в очередь."""
    label = 'альбом' if info.kind == 'album' else 'плейлист'
    header = f'Добавлен {label}' + (' (перемешано)' if shuffled else '')
    title = info.title or label.capitalize()
    description = _linked(f'**{title}**', info.url)
    description += f'\n{len(info.tracks)} треков'
    embed = discord.Embed(
        description=description,
        color=discord.Color.green(),
    )
    embed.set_author(name=header)
    if info.thumbnail:
        embed.set_thumbnail(url=info.thumbnail)
    return embed
