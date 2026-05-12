"""Сборка эмбедов «добавлено в очередь»."""
import discord

from audio import Track
from audio.track import PlaylistInfo


def _linked(text: str, url: str) -> str:
    return f'[{text}]({url})' if url else text


def _source_label(url: str) -> str:
    if not url:
        return ''
    if 'music.youtube.com' in url:
        return 'YouTube Music'
    if 'youtube.com' in url or 'youtu.be' in url:
        return 'YouTube'
    if 'open.spotify.com' in url:
        return 'Spotify'
    if 'music.yandex.' in url:
        return 'Yandex Music'
    if 'soundcloud.com' in url:
        return 'SoundCloud'
    return ''


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
    source = _source_label(track.url)
    if source:
        embed.set_footer(text=f'Источник: {source}')
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
    source_url = info.url or (info.tracks[0].url if info.tracks else '')
    source = _source_label(source_url)
    if source:
        embed.set_footer(text=f'Источник: {source}')
    return embed
