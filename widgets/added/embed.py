"""Сборка эмбедов «добавлено в очередь»"""
import discord

from audio import Track
from audio.track import PlaylistInfo

from ..format import source_line


def _linked(text: str, url: str) -> str:
    return f'[{text}]({url})' if url else text


def build_added_track_embed(track: Track, *, next_up: bool = False) -> discord.Embed:
    """Эмбед при добавлении одного трека"""
    title = track.title or 'Без названия'
    lines: list[str] = [f'## {_linked(title, track.url)}']
    if track.artist:
        lines.append(track.artist)
    source = source_line(track.url)
    if source:
        lines += ['', source]
    embed = discord.Embed(
        description='\n'.join(lines),
        color=discord.Color.green(),
    )
    embed.set_author(name='Играет следующим' if next_up else 'Добавлено в очередь')
    if track.thumbnail:
        embed.set_thumbnail(url=track.thumbnail)
    return embed


def build_added_playlist_embed(info: PlaylistInfo, *, shuffled: bool = False,
                               next_up: bool = False) -> discord.Embed:
    """Эмбед при добавлении плейлиста или альбома"""
    label = 'альбом' if info.kind == 'album' else 'плейлист'
    header = (f'Играет следующим {label}' if next_up else f'Добавлен {label}')
    header += ' (перемешано)' if shuffled else ''
    title = info.title or label.capitalize()
    lines: list[str] = [f'## {_linked(title, info.url)}']
    # у альбома исполнитель один и показывается так же, как у трека;
    # у плейлиста они разные, поэтому строки не будет
    if info.artist:
        lines.append(info.artist)
    lines.append(f'-# {len(info.tracks)} треков')
    source_url = info.url or (info.tracks[0].url if info.tracks else '')
    source = source_line(source_url)
    if source:
        lines += ['', source]
    embed = discord.Embed(
        description='\n'.join(lines),
        color=discord.Color.green(),
    )
    embed.set_author(name=header)
    if info.thumbnail:
        embed.set_thumbnail(url=info.thumbnail)
    return embed
