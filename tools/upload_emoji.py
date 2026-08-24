"""Разовая загрузка иконок сервисов как эмодзи приложения

Эмодзи приложения принадлежат боту, а не серверу: работают в любой гильдии,
где он есть, и не занимают серверных слотов. Загрузить их достаточно один раз,
дальше бот сам подтягивает разметку на старте.

Ищет PNG в resources/, имя эмодзи берёт из таблицы EMOJI_NAMES: Discord
принимает только буквы, цифры и подчёркивания, 2-32 символа.

Запуск:
    .venv/Scripts/python tools/upload_emoji.py            # показать, что будет
    .venv/Scripts/python tools/upload_emoji.py --apply    # загрузить
    .venv/Scripts/python tools/upload_emoji.py --replace  # перезалить существующие
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import discord

from private import discord_bot_token
from widgets.format.source import EMOJI_NAMES


_ROOT = Path(__file__).resolve().parent.parent
_RESOURCES = _ROOT / 'resources'
# ограничение Discord на файл эмодзи
_MAX_BYTES = 256 * 1024


def _wanted() -> dict:
    """имя эмодзи -> файл; сопоставляем по имени файла без учёта регистра"""
    files = {p.stem.lower().replace(' ', '_'): p
             for p in _RESOURCES.glob('*.png')}
    out = {}
    for label, name in EMOJI_NAMES.items():
        path = files.get(name) or files.get(label.lower().replace(' ', '_'))
        if path is not None:
            out[name] = path
        else:
            print(f'  нет файла для {label} (ожидался {name}.png) — '
                  f'останется текстовая метка')
    return out


async def main() -> int:
    ap = argparse.ArgumentParser(description='Загрузка иконок сервисов')
    ap.add_argument('--apply', action='store_true', help='действительно загрузить')
    ap.add_argument('--replace', action='store_true',
                    help='удалить и залить заново уже существующие')
    args = ap.parse_args()

    wanted = _wanted()
    if not wanted:
        print(f'В {_RESOURCES} нет подходящих PNG')
        return 1

    client = discord.Client(intents=discord.Intents.none())
    await client.login(discord_bot_token)
    try:
        existing = {e.name: e for e in await client.fetch_application_emojis()}
        print(f'\nуже загружено: {len(existing)}')
        for name, path in sorted(wanted.items()):
            blob = path.read_bytes()
            if len(blob) > _MAX_BYTES:
                print(f'  {name}: {len(blob)/1024:.0f} КБ больше лимита, пропуск')
                continue
            old = existing.get(name)
            if old is not None and not args.replace:
                print(f'  {name}: уже есть ({old}), пропуск')
                continue
            if not args.apply:
                print(f'  {name}: будет загружен из {path.name} '
                      f'({len(blob)/1024:.1f} КБ)')
                continue
            if old is not None:
                await old.delete()
            created = await client.create_application_emoji(name=name, image=blob)
            print(f'  {name}: загружен, разметка {created}')
        if not args.apply:
            print('\nэто был сухой прогон, добавьте --apply')
    finally:
        await client.close()
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
