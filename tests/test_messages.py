"""Тексты, которые бот отправляет в чат

Правило: сообщение не заканчивается точкой. Тест разбирает исходники и ловит
возврат точек при будущих правках
"""
import ast

import pytest

from conftest import ROOT
from widgets import plain_error


SOURCES = ('cogs/music.py', 'widgets/queue/view.py', 'client.py')
# обёртки кога над ctx.send: _private — личный ответ, _transient — публичный
# с самоудалением. Тексты живут в их вызовах, и сканер обязан знать их все,
# иначе проверка начнёт проходить впустую
SEND_CALLS = {'send', 'send_message', '_respond', '_private', '_transient'}


def _literal_tails(node):
    """Хвостовые строковые литералы аргумента: обычная строка, f-строка, тернарник"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value
    elif isinstance(node, ast.JoinedStr):
        if node.values and isinstance(node.values[-1], ast.Constant):
            yield node.values[-1].value
    elif isinstance(node, ast.IfExp):
        yield from _literal_tails(node.body)
        yield from _literal_tails(node.orelse)


def _chat_messages(rel: str) -> list[str]:
    tree = ast.parse((ROOT / rel).read_text(encoding='utf-8'))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, 'id', '')
        if name not in SEND_CALLS:
            continue
        for arg in node.args:
            found.extend(_literal_tails(arg))
    return found


@pytest.mark.parametrize('rel', SOURCES)
def test_chat_messages_do_not_end_with_period(rel):
    offenders = [m for m in _chat_messages(rel) if m.rstrip().endswith('.') and not m.rstrip().endswith('..')]
    assert not offenders, f'{rel}: точка в конце у {offenders}'


def test_scan_actually_finds_messages():
    """Страховка: если разбор сломается, предыдущий тест начнёт проходить впустую"""
    messages = _chat_messages('cogs/music.py')
    assert len(messages) > 20
    assert 'Пауза' in messages


@pytest.mark.parametrize('raw,expected', [
    ('Это не похоже на ссылку Spotify.', 'Это не похоже на ссылку Spotify'),
    ('Видео недоступно.', 'Видео недоступно'),
    ('Без точки', 'Без точки'),
    ('Загрузка...', 'Загрузка...'),
    ('Хвостовые пробелы.   ', 'Хвостовые пробелы'),
    ('', ''),
    ('.', ''),
])
def test_plain_error(raw, expected):
    assert plain_error(raw) == expected


def test_plain_error_accepts_exception():
    assert plain_error(RuntimeError('Трек Spotify недоступен.')) == 'Трек Spotify недоступен'


def test_plain_error_keeps_inner_periods():
    assert plain_error('Таймаут. Попробуй ещё раз.') == 'Таймаут. Попробуй ещё раз'
