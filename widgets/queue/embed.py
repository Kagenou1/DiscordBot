"""Эмбед страницы очереди"""
import discord

from audio import Track

from ..format import format_track_label


# лимит description у эмбеда — 4096, держим запас на заголовок строки и разметку
_DESCRIPTION_LIMIT = 3900
# подпись трека в строке очереди: длинные названия плюс URL выбивают лимит
_LABEL_LIMIT = 70


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def _line(index: int, track: Track) -> str:
    label = _clip(format_track_label(track), _LABEL_LIMIT)
    # экранируем ']' — иначе подпись рвёт markdown-ссылку
    label = label.replace('[', '(').replace(']', ')')
    return f'`{index:>2}.` [{label}]({track.url})' if track.url else f'`{index:>2}.` {label}'


def build_queue_embed(items: list[Track], *, page: int, page_size: int, max_page: int) -> discord.Embed:
    if not items:
        return discord.Embed(
            title='Очередь пуста',
            color=discord.Color.blurple(),
        )
    start = page * page_size
    end = min(start + page_size, len(items))

    lines: list[str] = []
    used = 0
    for i, t in enumerate(items[start:end], start=start):
        line = _line(i + 1, t)
        if used + len(line) + 1 > _DESCRIPTION_LIMIT:
            lines.append(f'-# …и ещё {end - i} на этой странице')
            break
        lines.append(line)
        used += len(line) + 1

    embed = discord.Embed(
        title=f'Очередь — {len(items)} треков',
        description='\n'.join(lines),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=f'Страница {page + 1} / {max_page + 1}')
    return embed
