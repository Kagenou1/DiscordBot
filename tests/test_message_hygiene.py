"""Что остаётся в канале после сессии

Требование: только сообщения о добавлении в очередь и карточка текущего трека.
Управляющие команды отвечают лично вызвавшему, служебные сообщения убираются
сами. Проверяется и разметка вызовов в исходнике, и поведение на дубле
"""
import ast

import pytest

from conftest import ROOT

SRC = ROOT / 'cogs' / 'music.py'

# видно только вызвавшему
PRIVATE = {'join', 'leave', 'clear', 'shuffle', 'pause', 'resume', 'skip',
           'skipto', 'repeat', 'queue_cmd', 'stop', 'seek', 'seekto', 'nowplaying'}
# остаётся в канале навсегда: это и есть след сессии. Не ответом команды,
# а обычным сообщением канала — иначе эмбед теряет обложку
PUBLIC = {'_added_card'}


def _calls_in(func: ast.FunctionDef) -> set[str]:
    return {n.func.attr for n in ast.walk(func)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}


def _functions() -> dict[str, ast.FunctionDef]:
    tree = ast.parse(SRC.read_text(encoding='utf-8'))
    return {n.name: n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


@pytest.mark.parametrize('name', sorted(PRIVATE))
def test_управляющие_команды_отвечают_лично(name):
    """Иначе подтверждения копятся в канале"""
    func = _functions()[name]
    calls = _calls_in(func)
    assert '_respond' not in calls, f'/{name} отвечает публично и навсегда'


def test_added_card_goes_to_the_channel():
    """Карточка добавления остаётся в канале, но ответом команды не является

    Правка ответа взаимодействия не проксирует внешние картинки: обложка
    появлялась и через секунду исчезала. Проверено на Yandex и VK — разные
    CDN, поведение одинаковое; тот же эмбед обычным сообщением канала
    обложку держит
    """
    calls = _calls_in(_functions()['_enqueue'])
    assert '_added_card' in calls, 'карточка добавления не уходит в канал'
    assert '_respond' not in calls, 'ответом взаимодействия карточка теряет обложку'

    card = _calls_in(_functions()['_added_card'])
    assert 'send' in card, 'у слэш-команды карточка идёт followup-ом'
    assert 'reply' in card, 'у текстовой команды — ответом на сообщение с командой'
    assert '_private' not in card, 'текст ради закрытия взаимодействия не нужен'
    assert 'delete_original_response' not in card, (
        'первый followup занимает слот заглушки: удаление сотрёт саму карточку')


# Слот заглушки занимает ПЕРВЫЙ ответ, а эфемерность задаётся дефёром и ответом
# уже не переопределяется. Публично дефёрит только путь play/playnext: там слот
# занимает карточка добавления, и она обязана быть видна всем
PUBLIC_DEFER = {'_enqueue', '_ensure_voice'}


def test_defer_matches_what_fills_the_placeholder():
    """Дефёр и первый ответ обязаны совпадать по видимости

    Обе ошибки уже случились: публичный дефёр в before_invoke-хуке сделал
    публичным «Добавлено в очередь», а личный — личной саму карточку.
    Проверяется весь файл, а не список команд: заглушку ставит и хук
    `_ensure_voice`, который командой не является
    """
    src = SRC.read_text(encoding='utf-8')
    wrong = []
    for name, func in _functions().items():
        seg = ast.get_source_segment(src, func) or ''
        if '_ensure_deferred' not in seg:
            continue
        is_public = '_ensure_deferred(ctx)' in seg
        if is_public != (name in PUBLIC_DEFER):
            wrong.append(f'{name}: дефёр {"публичный" if is_public else "личный"}')
    assert not wrong, wrong


async def test_личный_ответ_помечается_эфемерным(cog, ctx):
    """Слэш-команда: ответ виден только вызвавшему и в канале не оседает"""
    from conftest import SlottedInteraction

    # взаимодействие уже отвечено, иначе первое сообщение уйдёт в правку заглушки
    inter = SlottedInteraction(iid=42)
    ctx.interaction = inter
    cog._answered[inter.id] = None

    await cog._private(ctx, 'Пауза')

    assert ctx.sent[-1].ephemeral is True


async def test_личный_ответ_в_текстовой_команде_убирается_сам(cog, ctx, monkeypatch):
    """Эфемерных сообщений у текстовых команд не бывает — убираем по таймеру"""
    monkeypatch.setattr(ctx, 'interaction', None)
    await cog._private(ctx, 'Пауза')
    msg = ctx.sent[-1]
    assert msg.ephemeral is False
    assert msg.delete_after, 'текстовый ответ останется в канале навсегда'


async def test_служебное_сообщение_убирается_само(cog, ctx):
    await cog._transient(ctx, 'Пропускаю трек')
    assert ctx.sent[-1].delete_after


async def test_публичный_ответ_остаётся(cog, ctx):
    await cog._respond(ctx, 'Добавлено в очередь')
    msg = ctx.sent[-1]
    assert msg.ephemeral is False
    assert msg.delete_after is None


def test_playnext_подключается_к_каналу_как_play():
    """Без before_invoke команда не поднимет бота в голосовой канал"""
    src = SRC.read_text(encoding='utf-8')
    assert '@playnext.before_invoke' in src, 'playnext не подключается к каналу'


def test_playnext_отвечает_публично():
    """Его карточка — такой же след сессии, как у play"""
    funcs = _functions()
    seg = ast.get_source_segment(SRC.read_text(encoding='utf-8'), funcs['playnext']) or ''
    assert 'front=True' in seg, 'playnext добавляет в конец, а должен в голову'
    assert '_private' not in seg
