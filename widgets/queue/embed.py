"""Эмбед страницы очереди."""
import discord

from audio import Track

from ..format import format_track_label


def build_queue_embed(items: list[Track], *, page: int, page_size: int, max_page: int) -> discord.Embed:
    if not items:
        return discord.Embed(
            title='Очередь пуста',
            color=discord.Color.blurple(),
        )
    start = page * page_size
    end = min(start + page_size, len(items))
    lines = [
        f'`{i + 1:>2}.` [{format_track_label(t)}]({t.url})' if t.url
        else f'`{i + 1:>2}.` {format_track_label(t)}'
        for i, t in enumerate(items[start:end], start=start)
    ]
    embed = discord.Embed(
        title=f'Очередь — {len(items)} треков',
        description='\n'.join(lines),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=f'Страница {page + 1} / {max_page + 1}')
    return embed
