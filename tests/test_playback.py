"""Машина состояний воспроизведения: очередь, повтор, предзагрузка, перемотка

Всё на заглушках, без сети и без ffmpeg. Проверяются переходы и узкие случаи,
а не окружение
"""
import asyncio
import types

import discord
import pytest

from conftest import track
from cogs.music import END_OF_TRACK_EPSILON_SECONDS, MAX_PLAY_ATTEMPTS


class FakeSource:
    """Минимальный OpusAudioSource: только то, что читает ког"""

    def __init__(self, title='S', duration=200.0, reason=None, ready=True, extractor=''):
        # extractor пустой по умолчанию: так выглядят источники Yandex
        # и SoundCloud, и ротация профиля YouTube их не касается
        self.data = {'duration': duration, 'url': f'https://stream/{title}',
                     'acodec': 'opus', 'extractor': extractor}
        self.title = title
        self.cleaned = 0
        self._reason = reason
        self._ready = ready
        self.ready_calls = 0

    def cleanup(self):
        self.cleaned += 1

    def failure_reason(self):
        return self._reason

    def wait_ready(self, timeout=None):
        """Ког ждёт звука до play(); дубль обязан уметь то же самое"""
        self.ready_calls += 1
        return self._ready


def make_track(title='S', duration=200.0, sources=None):
    """Track с резолвером, отдающим заранее заготовленные источники

    У настоящих резолверов есть атрибут data — шаг «добыть данные, не поднимая
    ffmpeg», которым пользуется заготовка. Дубль без него молча выключил бы
    заготовку, и тесты перехода проверяли бы не то
    """
    t = track(title=title, url=f'https://x/{title}', duration=duration)
    made = []

    def _next(tr):
        src = (sources.pop(0) if sources else None) or FakeSource(tr.title, duration)
        made.append(src)
        return src

    async def _resolver(tr, *, loop=None, timeout=30):
        return _next(tr)

    async def _resolve_data(tr, *, loop=None, timeout=30):
        return {'url': f'https://stream/{tr.title}', 'source': _next(tr)}

    _resolver.data = _resolve_data
    t.resolver = _resolver
    t.made = made
    return t


@pytest.fixture(autouse=True)
def _source_from_data(monkeypatch):
    """make_source на кэше заготовки зовёт настоящий from_resolved

    Он поднял бы ffmpeg, поэтому отдаём дубль, положенный в те же данные
    """
    from audio.source import OpusAudioSource
    monkeypatch.setattr(OpusAudioSource, 'from_resolved',
                        classmethod(lambda cls, data, **kw: data['source']))


@pytest.fixture(autouse=True)
async def _no_stray_tasks(cog):
    """Ког плодит фоновые задачи; гасим их, чтобы не текли между тестами"""
    yield
    for task in list(cog._bg_tasks) + list(cog._np_task.values()) \
            + list(cog._idle_tasks.values()) + list(cog._alone_tasks.values()):
        task.cancel()
    await asyncio.sleep(0)


# --- базовый переход --------------------------------------------------------

async def test_plays_first_track_from_queue(cog, ctx):
    t = make_track('первый')
    cog.get_queue(ctx.guild.id).append(t)
    await cog._play_next_locked(ctx)

    assert ctx.voice_client.played, 'источник не ушёл в плеер'
    assert cog._current[ctx.guild.id] is t
    assert len(cog.get_queue(ctx.guild.id)) == 0
    assert any(m.embed is not None for m in ctx.channel.sent), 'нет карточки «сейчас играет»'


async def test_empty_queue_starts_idle_timer(cog, ctx):
    gid = ctx.guild.id
    await cog._play_next_locked(ctx)
    assert not ctx.voice_client.played
    assert gid not in cog._current
    assert gid in cog._idle_tasks, 'при пустой очереди должен пойти отсчёт простоя'


async def test_failed_track_is_skipped_and_reported(cog, ctx):
    """Один трек не резолвится — переходим к следующему, а не падаем"""
    bad = track(title='битый', url='https://x/bad')

    async def _boom(tr, *, loop=None, timeout=30):
        raise RuntimeError('не открылся')

    bad.resolver = _boom
    good = make_track('рабочий')
    cog.get_queue(ctx.guild.id).extend([bad, good])

    await cog._play_next_locked(ctx)

    assert cog._current[ctx.guild.id] is good
    assert any('битый' in (m.content or '') for m in ctx.sent)


async def test_disconnect_during_resolve_drops_source(cog, ctx):
    """Резолв идёт по сети; если бота выдернули, источник надо погасить"""
    src = FakeSource('трек')

    async def _slow(tr, *, loop=None, timeout=30):
        ctx.voice_client = None  # выдернули, пока резолвили
        return src

    t = track(title='трек', url='https://x/t')
    t.resolver = _slow
    cog.get_queue(ctx.guild.id).append(t)

    await cog._play_next_locked(ctx)

    assert src.cleaned == 1, 'источник осиротел бы вместе с ffmpeg'
    assert ctx.guild.id not in cog._current


# --- режимы повтора ---------------------------------------------------------

async def test_repeat_track_requeues_current(cog, ctx):
    gid = ctx.guild.id
    t = make_track('повторяемый')
    cog.get_queue(gid).append(t)
    await cog._play_next_locked(ctx)

    cog._loop_modes[gid] = 'track'
    ctx.voice_client._playing = False
    await cog._play_next_locked(ctx)

    assert cog._current[gid] is t, 'трек должен зазвучать снова'
    assert len(t.made) == 2, 'на второй круг нужен свежий источник'


async def test_skip_cancels_repeat_track_once(cog, ctx):
    """/skip при repeat track пропускает трек, но не выключает режим"""
    gid = ctx.guild.id
    current, nxt = make_track('текущий'), make_track('следующий')
    cog.get_queue(gid).append(current)
    await cog._play_next_locked(ctx)

    cog._loop_modes[gid] = 'track'
    cog._skip_loop_once.add(gid)
    cog.get_queue(gid).append(nxt)
    ctx.voice_client._playing = False
    await cog._play_next_locked(ctx)

    assert cog._current[gid] is nxt
    assert gid not in cog._skip_loop_once, 'флаг одноразовый'
    assert cog._loop_modes[gid] == 'track', 'сам режим остаётся'


async def test_repeat_queue_refills_when_exhausted(cog, ctx):
    gid = ctx.guild.id
    a, b = make_track('A'), make_track('B')
    cog.get_queue(gid).extend([a, b])
    cog._loop_modes[gid] = 'queue'

    await cog._play_next_locked(ctx)          # играет A, в _played пусто
    ctx.voice_client._playing = False
    await cog._play_next_locked(ctx)          # A уходит в _played, играет B
    assert cog._current[gid] is b
    ctx.voice_client._playing = False
    await cog._play_next_locked(ctx)          # очередь пуста -> круг заново

    assert cog._current[gid] is a, 'после исчерпания очередь должна начаться сначала'
    assert not cog._played.get(gid), '_played должен быть перелит обратно в очередь'


async def test_repeat_queue_resets_track_cache(cog, ctx):
    """Стрим-URL к следующему кругу протухнет, кэш надо сбросить"""
    gid = ctx.guild.id
    a = make_track('A')
    cog.get_queue(gid).append(a)
    cog._loop_modes[gid] = 'queue'
    await cog._play_next_locked(ctx)

    a.cache_resolved({'url': 'старый-стрим'})
    ctx.voice_client._playing = False
    await cog._play_next_locked(ctx)

    assert a._resolved is None and a._resolved_at == 0.0


# --- предзагрузка -----------------------------------------------------------

async def test_prefetched_source_is_adopted(cog, ctx):
    """Заготовленный источник берут как есть: переход обязан быть мгновенным

    Ради этого он и поднимается заранее. Замер сборки на месте: 226 мс на
    copy-пути и 248 мс на перекодировании
    """
    gid = ctx.guild.id
    t = make_track('предзагруженный')
    cog.get_queue(gid).append(t)
    await cog._prefetch(gid)
    prepared = cog._prefetched[gid][1]

    await cog._play_next_locked(ctx)

    assert ctx.voice_client.played[-1] is prepared, 'готовый источник не использован'
    assert prepared.cleaned == 0
    assert len(t.made) == 1, 'повторный резолв не нужен'


async def test_stale_prefetch_is_discarded(cog, ctx):
    """Очередь перетасовали — заготовка больше не для головы очереди"""
    gid = ctx.guild.id
    stale_track, actual = make_track('устаревший'), make_track('актуальный')
    stale_source = FakeSource('устаревший')
    cog._prefetched[gid] = (stale_track, stale_source)
    cog.get_queue(gid).append(actual)

    await cog._play_next_locked(ctx)

    assert stale_source.cleaned == 1, 'висящий ffmpeg заготовки не погашен'
    assert cog._current[gid] is actual


async def test_prefetch_stores_next_track(cog, ctx):
    gid = ctx.guild.id
    nxt = make_track('следующий')
    cog.get_queue(gid).append(nxt)

    await cog._prefetch(gid)

    assert cog._prefetched[gid][0] is nxt
    assert cog._prefetched[gid][1] is nxt.made[0], 'источник не поднят заранее'


async def test_prefetch_discards_result_if_queue_changed(cog, ctx):
    gid = ctx.guild.id
    t = track(title='был первым', url='https://x/1')

    async def _resolver(tr, *, loop=None, timeout=30):
        raise AssertionError('заготовка не должна поднимать источник')

    async def _resolve_data(tr, *, loop=None, timeout=30):
        cog.get_queue(gid).clear()  # очередь ушла из-под ног, пока резолвили
        return {'url': 'https://stream/1', 'source': FakeSource('был первым')}

    _resolver.data = _resolve_data
    t.resolver = _resolver
    cog.get_queue(gid).append(t)

    await cog._prefetch(gid)

    assert gid not in cog._prefetched
    assert t._resolved is None, 'данные не про эту голову, держать их нельзя'


async def test_prefetch_is_not_repeated(cog, ctx):
    gid = ctx.guild.id
    cog._prefetched[gid] = make_track('уже есть')
    t = make_track('другой')
    cog.get_queue(gid).append(t)

    await cog._prefetch(gid)

    assert t._resolved is None, 'при готовой заготовке резолвить нечего'


# --- перемотка --------------------------------------------------------------

async def test_seek_past_end_skips_track(cog, ctx):
    gid = ctx.guild.id
    cog._current[gid] = track(title='T')
    cog._np_source[gid] = FakeSource('T', duration=100.0)

    reply = await cog._apply_seek(ctx, 100 - END_OF_TRACK_EPSILON_SECONDS, log_label='seek')

    assert ctx.voice_client.stopped == 1
    assert 'пропущен' in reply.lower()


async def test_seek_without_source_is_reported(cog, ctx):
    reply = await cog._apply_seek(ctx, 10, log_label='seek')
    assert 'не могу перемотать' in reply.lower()
    assert ctx.voice_client.stopped == 0


async def test_seek_rebuilds_source_and_resets_clock(cog, ctx, monkeypatch):
    gid = ctx.guild.id
    old = FakeSource('T', duration=300.0)
    cog._current[gid] = track(title='T')
    cog._np_source[gid] = old
    ctx.voice_client._playing = True

    new = FakeSource('T', duration=300.0)
    monkeypatch.setattr('cogs.music.YTDLSource.from_resolved',
                        classmethod(lambda cls, data, *, start=0.0: new))
    monkeypatch.setattr(cog, '_schedule_source_cleanup', lambda src, delay=2.0: None)

    reply = await cog._apply_seek(ctx, 120.0, log_label='seekto')

    assert ctx.voice_client.source is new
    assert cog._np_source[gid] is new
    assert cog._elapsed(gid) == pytest.approx(120, abs=1)
    assert '2:00' in reply


async def test_seek_keeps_pause(cog, ctx, monkeypatch):
    """set_source() внутри discord.py делает resume() безусловно"""
    gid = ctx.guild.id
    cog._current[gid] = track(title='T')
    cog._np_source[gid] = FakeSource('T', duration=300.0)
    ctx.voice_client._paused = True
    ctx.voice_client._playing = False

    monkeypatch.setattr('cogs.music.YTDLSource.from_resolved',
                        classmethod(lambda cls, data, *, start=0.0: FakeSource('T', 300.0)))
    monkeypatch.setattr(cog, '_schedule_source_cleanup', lambda src, delay=2.0: None)

    await cog._apply_seek(ctx, 60.0, log_label='seekto')

    assert ctx.voice_client.is_paused(), 'пауза должна пережить перемотку'


# --- сообщение о сбое -------------------------------------------------------

@pytest.fixture
def no_advance(cog, monkeypatch):
    """_advance в конце зовёт _play_next; в этих тестах он не интересен"""
    monkeypatch.setattr(cog, '_play_next', lambda c: asyncio.sleep(0))


async def test_failure_requeues_track_for_retry(cog, ctx, no_advance):
    """403 от googlevideo транзиентен: свежая ссылка обычно играет"""
    gid = ctx.guild.id
    t = track(title='Песня', artist='Артист')
    t.cache_resolved({'url': 'протухший-стрим'})
    cog._current[gid] = t

    await cog._advance(ctx, 'FFmpeg exited with code 1')

    assert cog.get_queue(gid)[0] is t, 'трек должен вернуться в голову очереди'
    assert t._play_attempts == 1
    assert t._resolved is None, 'кэш обязан быть сброшен, иначе повторим тот же URL'
    assert ctx.sent == [], 'первая неудача пользователя не беспокоит'


async def test_failure_drops_prefetch(cog, ctx, no_advance):
    """Соседняя ссылка выдана в той же пачке и протухла так же"""
    gid = ctx.guild.id
    cog._current[gid] = track(title='T')
    stale = FakeSource('следующий')
    cog._prefetched[gid] = (make_track('следующий'), stale)

    await cog._advance(ctx, 'FFmpeg exited with code 1')

    assert gid not in cog._prefetched
    assert stale.cleaned == 1, 'висящий ffmpeg соседа не погашен'


async def test_failure_reported_after_attempts_exhausted(cog, ctx, no_advance):
    from cogs.music import MAX_PLAY_ATTEMPTS

    gid = ctx.guild.id
    t = track(title='Песня', artist='Артист')
    t._play_attempts = MAX_PLAY_ATTEMPTS
    cog._current[gid] = t

    await cog._advance(ctx, 'FFmpeg exited with code 1.')

    assert len(cog.get_queue(gid)) == 0, 'исчерпав попытки, трек уходит'
    assert len(ctx.sent) == 1
    text = ctx.sent[0].content
    assert 'Песня' in text and 'FFmpeg exited with code 1' in text
    assert not text.endswith('.'), 'точка в конце сообщения не нужна'


async def test_retry_stops_after_limit(cog, ctx, no_advance):
    """Битый навсегда трек не должен крутиться в очереди бесконечно"""
    from cogs.music import MAX_PLAY_ATTEMPTS

    gid = ctx.guild.id
    t = track(title='битый')
    for _ in range(MAX_PLAY_ATTEMPTS + 1):
        # настоящий _play_next_locked достаёт трек из очереди и делает текущим;
        # без этого шага _advance со второго круга видел бы пустоту
        cog._current[gid] = t
        cog.get_queue(gid).clear()
        await cog._advance(ctx, 'FFmpeg exited with code 1')
    assert len(ctx.sent) == 1, 'ровно одно сообщение, когда попытки кончились'


async def test_repeat_track_does_not_double_requeue(cog, ctx, no_advance):
    """Трек, возвращённый ретраем, не должен попасть в очередь ещё и от
    _play_next_locked: тот берёт его из _current, поэтому _advance обязан
    _current очистить"""
    gid = ctx.guild.id
    t = track(title='Песня')
    cog._current[gid] = t
    cog._loop_modes[gid] = 'track'

    await cog._advance(ctx, 'FFmpeg exited with code 1')

    assert list(cog.get_queue(gid)) == [t], 'ретрай обязан вернуть трек в очередь'
    assert gid not in cog._current, '_current не очищен: _play_next_locked добавит трек второй раз'


async def test_successful_track_resets_attempts(cog, ctx, no_advance):
    gid = ctx.guild.id
    t = track(title='Песня')
    t._play_attempts = 1
    cog._current[gid] = t

    await cog._advance(ctx, None)

    assert t._play_attempts == 0
    assert ctx.sent == []


# --- одиночество и простой --------------------------------------------------

async def test_auto_pause_when_left_alone(cog, ctx):
    gid = ctx.guild.id
    vc = ctx.voice_client
    vc._playing = True
    vc.channel = type('Ch', (), {'members': []})()
    cog.bot._guild.voice_client = vc

    await cog._reevaluate_alone(gid)

    assert vc.is_paused()
    assert gid in cog._auto_paused
    assert gid in cog._alone_tasks, 'таймер авто-выхода не запущен'


async def test_auto_resume_when_someone_returns(cog, ctx):
    gid = ctx.guild.id
    vc = ctx.voice_client
    human = type('M', (), {'bot': False})()
    vc._paused = True
    vc.channel = type('Ch', (), {'members': [human]})()
    cog.bot._guild.voice_client = vc
    cog._auto_paused.add(gid)

    await cog._reevaluate_alone(gid)

    assert not vc.is_paused()
    assert gid not in cog._auto_paused
    assert gid not in cog._alone_tasks


async def test_manual_pause_survives_return(cog, ctx):
    """Ручную паузу возвращение слушателя снимать не должно"""
    gid = ctx.guild.id
    vc = ctx.voice_client
    human = type('M', (), {'bot': False})()
    vc._paused = True
    vc.channel = type('Ch', (), {'members': [human]})()
    cog.bot._guild.voice_client = vc
    # _auto_paused НЕ выставлен: паузу поставил человек

    await cog._reevaluate_alone(gid)

    assert vc.is_paused()


@pytest.mark.parametrize('playing,paused,queued,expect_timer', [
    (False, False, False, True),   # ничего не играет и очередь пуста -> простой
    (True, False, False, False),   # играет
    (False, True, False, False),   # на паузе
    (False, False, True, False),   # есть очередь
])
async def test_idle_timer_follows_state(cog, ctx, playing, paused, queued, expect_timer):
    gid = ctx.guild.id
    vc = ctx.voice_client
    vc._playing, vc._paused = playing, paused
    vc.channel = object()
    cog.bot._guild.voice_client = vc
    if queued:
        cog.get_queue(gid).append(track())

    cog._refresh_idle_timer(gid)

    assert (gid in cog._idle_tasks) is expect_timer


async def test_idle_timer_dropped_when_disconnected(cog, ctx):
    gid = ctx.guild.id
    cog._idle_tasks[gid] = asyncio.create_task(asyncio.sleep(60))
    cog.bot._guild.voice_client = None

    cog._refresh_idle_timer(gid)

    assert gid not in cog._idle_tasks


# --- переключение клиента YouTube при сбое ----------------------------------

async def test_youtube_failure_rotates_client(cog, ctx, no_advance, monkeypatch):
    """Перевыдача тем же клиентом даст тот же отказ, если YouTube его разлюбил"""
    rotated = []
    monkeypatch.setattr('cogs.music.rotate_stream_client', lambda: rotated.append(1) or 'default')
    cog._current[ctx.guild.id] = track(title='T')

    await cog._advance(ctx, 'FFmpeg exited with code 1', from_youtube=True)

    assert rotated, 'профиль клиента не переключили'


async def test_non_youtube_failure_does_not_rotate(cog, ctx, no_advance, monkeypatch):
    """Yandex и SoundCloud идут через свои клиенты, крутить чужой профиль незачем"""
    rotated = []
    monkeypatch.setattr('cogs.music.rotate_stream_client', lambda: rotated.append(1) or 'x')
    cog._current[ctx.guild.id] = track(title='T')

    await cog._advance(ctx, 'FFmpeg exited with code 1', from_youtube=False)

    assert not rotated


async def test_no_rotation_on_success(cog, ctx, no_advance, monkeypatch):
    rotated = []
    monkeypatch.setattr('cogs.music.rotate_stream_client', lambda: rotated.append(1) or 'x')
    cog._current[ctx.guild.id] = track(title='T')

    await cog._advance(ctx, None, from_youtube=True)

    assert not rotated


async def test_after_play_detects_youtube_source(cog, ctx, monkeypatch):
    """from_youtube берётся из поля extractor, которое проставляет yt-dlp"""
    captured = {}

    async def _fake_advance(c, reason, *, from_youtube=False):
        captured.update(reason=reason, from_youtube=from_youtube)

    monkeypatch.setattr(cog, '_advance', _fake_advance)
    cog.bot.loop = asyncio.get_running_loop()

    src = FakeSource('T', reason='FFmpeg exited with code 1')
    src.data['extractor'] = 'youtube'
    cog._after_play(ctx, None, src)
    await asyncio.sleep(0.05)

    assert captured['from_youtube'] is True
    assert 'FFmpeg' in captured['reason']


async def test_after_play_marks_yandex_source(cog, ctx, monkeypatch):
    captured = {}

    async def _fake_advance(c, reason, *, from_youtube=False):
        captured.update(from_youtube=from_youtube)

    monkeypatch.setattr(cog, '_advance', _fake_advance)
    cog.bot.loop = asyncio.get_running_loop()

    src = FakeSource('T', reason='FFmpeg exited with code 1')  # без extractor, как у Yandex
    cog._after_play(ctx, None, src)
    await asyncio.sleep(0.05)

    assert captured['from_youtube'] is False


# --- ответ на взаимодействие ------------------------------------------------

from conftest import SlottedInteraction as FakeInteraction  # noqa: E402


async def test_first_message_replaces_deferred_placeholder(cog, ctx):
    """Иначе followup ссылается на заглушку и Discord пишет «Не удается загрузить»"""
    inter = FakeInteraction()
    ctx.interaction = inter

    await cog._respond(ctx, 'первое')
    assert len(inter.edited) == 1
    assert inter.edited[0]['content'] == 'первое'
    assert ctx.sent == [], 'первое сообщение не должно уходить followup-ом'


async def test_later_messages_go_as_followups(cog, ctx):
    inter = FakeInteraction()
    ctx.interaction = inter

    await cog._respond(ctx, 'первое')
    await cog._respond(ctx, 'второе')
    await cog._respond(ctx, embed='карточка')

    assert len(inter.edited) == 1, 'исходный ответ заменяется ровно один раз'
    assert [m.content for m in ctx.sent] == ['второе', None]


async def test_respond_falls_back_when_edit_fails(cog, ctx):
    """Взаимодействие могло истечь — сообщение всё равно должно дойти"""
    ctx.interaction = FakeInteraction(fail=True)
    await cog._respond(ctx, 'текст')
    assert [m.content for m in ctx.sent] == ['текст']


async def test_respond_without_interaction(cog, ctx):
    """Текстовая команда: заглушки нет, идём обычным путём"""
    ctx.interaction = None
    await cog._respond(ctx, 'текст')
    assert [m.content for m in ctx.sent] == ['текст']


# --- удаление доигравшей карточки -------------------------------------------

async def test_finished_card_removed_on_next_track(cog, ctx):
    """Плейлист на сотню треков иначе оставит сотню мёртвых прогресс-баров"""
    gid = ctx.guild.id
    a, b = make_track('A'), make_track('B')
    cog.get_queue(gid).extend([a, b])

    await cog._play_next_locked(ctx)
    first_card = cog._np_msg[gid]
    ctx.voice_client._playing = False
    await cog._play_next_locked(ctx)

    assert first_card.deleted is True
    assert cog._np_msg[gid] is not first_card


async def test_last_card_survives(cog, ctx):
    """Очередь кончилась — карточка остаётся финальным снимком"""
    gid = ctx.guild.id
    cog.get_queue(gid).append(make_track('A'))
    await cog._play_next_locked(ctx)
    card = cog._np_msg[gid]
    ctx.voice_client._playing = False
    await cog._play_next_locked(ctx)

    assert card.deleted is False
    assert gid in cog._np_finished, 'карточка ждёт следующего трека, но его нет'


async def test_card_never_goes_through_interaction(cog, ctx):
    """Регрессия: followup живёт токеном команды, а карточка — дольше

    Через 15 минут после команды Discord перестаёт отдавать и правку, и
    удаление followup'а: карточка замирала на полпути и висела вечно
    """
    gid = ctx.guild.id
    ctx.interaction = FakeInteraction()
    cog.get_queue(gid).append(make_track('A'))

    await cog._play_next_locked(ctx)

    card = cog._np_msg[gid]
    assert card in ctx.channel.sent, 'карточка должна уходить прямо в канал'
    assert card not in ctx.sent, 'карточка ушла через взаимодействие'
    assert card.id not in cog._original_msgs


async def test_original_response_is_never_deleted(cog, ctx):
    """Регрессия: удаление исходного ответа возвращает «Не удается загрузить»

    Карточкой исходный ответ больше не бывает, но страховка в
    _drop_finished_card обязана держать это правило и дальше
    """
    gid = ctx.guild.id
    cog.get_queue(gid).extend([make_track('A'), make_track('B')])

    await cog._play_next_locked(ctx)
    first_card = cog._np_msg[gid]
    cog._remember(cog._original_msgs, first_card.id)  # как будто им всё же стала

    ctx.voice_client._playing = False
    await cog._play_next_locked(ctx)

    assert first_card.deleted is False, 'исходный ответ трогать нельзя'


async def test_reset_forgets_pending_card(cog, ctx):
    gid = ctx.guild.id
    cog.get_queue(gid).append(make_track('A'))
    await cog._play_next_locked(ctx)
    ctx.voice_client._playing = False
    await cog._play_next_locked(ctx)
    assert gid in cog._np_finished

    await cog._reset_session(gid)
    assert gid not in cog._np_finished


async def test_no_edit_without_defer(cog, ctx):
    """Регрессия: /skip не дефёрит, и правка исходного ответа сжигала окно ответа

    edit_original_response на неотвеченном взаимодействии уходит в Discord,
    возвращает 404 и съедает три секунды — команда падает с Unknown interaction
    """
    ctx.interaction = FakeInteraction(deferred=False)

    await cog._respond(ctx, 'Трек пропущен')

    assert ctx.interaction.edited == [], 'к Discord ходить было незачем'
    assert [m.content for m in ctx.sent] == ['Трек пропущен']
    assert ctx.interaction.id not in cog._answered, 'взаимодействие не помечаем как отвеченное'


async def test_deferred_command_still_replaces_placeholder(cog, ctx):
    """У дефёрнутой команды заглушка есть, и первое сообщение обязано её заменить"""
    ctx.interaction = FakeInteraction(deferred=True)

    await cog._respond(ctx, 'первое')

    assert len(ctx.interaction.edited) == 1
    assert ctx.sent == []


# --- гонка предзагрузки и перехода к треку ----------------------------------

def slow_track(title, delay, resolved):
    """Track, чей резолв занимает время и считает свои вызовы"""
    t = track(title=title, url=f'https://x/{title}')
    calls = []

    async def _resolve_data(tr, *, loop=None, timeout=30):
        calls.append(1)
        await asyncio.sleep(delay)
        src = FakeSource(title)
        resolved.append(src)
        return {'url': f'https://stream/{title}', 'source': src}

    async def _resolver(tr, *, loop=None, timeout=30):
        return (await _resolve_data(tr, loop=loop, timeout=timeout))['source']

    _resolver.data = _resolve_data
    t.resolver = _resolver
    t.calls = calls
    return t


async def test_waits_for_inflight_prefetch(cog, ctx):
    """Регрессия: трек резолвился дважды — заготовкой и переходом одновременно

    В логе это выглядело как play_next -> playing … (prefetched=False), а через
    две секунды второй spotify->yt того же трека и второй ffmpeg
    """
    gid = ctx.guild.id
    made = []
    t = slow_track('A', 0.15, made)
    cog.get_queue(gid).append(t)

    cog._spawn(cog._prefetch(gid))
    await asyncio.sleep(0.02)          # заготовка ещё в полёте
    assert gid in cog._prefetching

    await cog._play_next_locked(ctx)

    assert len(t.calls) == 1, f'трек зарезолвлен {len(t.calls)} раз(а) вместо одного'
    assert len(made) == 1, 'лишний источник ffmpeg не должен создаваться'
    assert ctx.voice_client.played[-1] is made[0], 'должна использоваться заготовка'


async def test_does_not_wait_for_other_track(cog, ctx):
    """Заготовка другого трека переходу не помеха"""
    gid = ctx.guild.id
    other = slow_track('другой', 5.0, [])
    cog._prefetching[gid] = (other, cog._spawn(asyncio.sleep(5)))
    cog.get_queue(gid).append(make_track('нужный'))

    await asyncio.wait_for(cog._play_next_locked(ctx), timeout=1.0)

    assert cog._current[gid].title == 'нужный'


async def test_failed_prefetch_does_not_block_playback(cog, ctx):
    """Заготовка упала — переход обязан состояться сам"""
    gid = ctx.guild.id
    t = track(title='битый', url='https://x/b')
    attempts = []

    async def _resolve_data(tr, *, loop=None, timeout=30):
        attempts.append(1)
        await asyncio.sleep(0.05)
        if len(attempts) == 1:
            raise RuntimeError('заготовка не смогла')
        return {'url': 'https://stream/b', 'source': FakeSource('битый')}

    async def _resolver(tr, *, loop=None, timeout=30):
        return (await _resolve_data(tr, loop=loop, timeout=timeout))['source']

    _resolver.data = _resolve_data
    t.resolver = _resolver
    cog.get_queue(gid).append(t)

    cog._spawn(cog._prefetch(gid))
    await asyncio.sleep(0.01)
    await cog._play_next_locked(ctx)

    assert len(attempts) == 2, 'после неудачной заготовки переход резолвит сам'
    assert cog._current[gid] is t


async def test_prefetch_not_started_twice(cog, ctx):
    gid = ctx.guild.id
    t = slow_track('A', 0.1, [])
    cog.get_queue(gid).append(t)

    first = cog._spawn(cog._prefetch(gid))
    await asyncio.sleep(0.01)
    await cog._prefetch(gid)           # второй заход при живой заготовке
    await first

    assert len(t.calls) == 1


async def test_reset_cancels_inflight_prefetch(cog, ctx):
    gid = ctx.guild.id
    cog.get_queue(gid).append(slow_track('A', 5.0, []))
    task = cog._spawn(cog._prefetch(gid))
    await asyncio.sleep(0.01)

    await cog._reset_session(gid)
    await asyncio.sleep(0)

    assert task.cancelled() or task.done()
    assert gid not in cog._prefetching


async def test_id_memory_is_bounded(cog):
    """Бот работает сутками: множества id не должны расти без предела"""
    from cogs.music import ID_MEMORY

    for i in range(ID_MEMORY * 3):
        cog._remember(cog._answered, i)

    assert len(cog._answered) == ID_MEMORY
    assert 0 not in cog._answered, 'самые старые должны вытесняться'
    assert ID_MEMORY * 3 - 1 in cog._answered, 'свежие остаются'


async def test_remember_keeps_recent_interaction(cog, ctx):
    """Вытеснение не должно ломать защиту исходного ответа в пределах трека"""
    ctx.interaction = FakeInteraction(iid=7)
    await cog._respond(ctx, 'первое')
    assert 7 in cog._answered

    await cog._respond(ctx, 'второе')
    assert len(ctx.interaction.edited) == 1, 'заглушка заменяется один раз'


# --- звук должен идти до старта плеера ---------------------------------------

async def test_плеер_не_стартует_с_пустым_буфером(cog, ctx):
    """Плеер тактируется абсолютным временем от play(): старт с пустым буфером
    превращает заминку ffmpeg не в задержку, а в тишину поверх отсчёта"""
    good = FakeSource('S', ready=True)
    cog.get_queue(ctx.guild.id).append(make_track('S', sources=[good]))

    await cog._play_next_locked(ctx)

    assert good.ready_calls == 1, 'готовность не проверили'
    assert ctx.voice_client.source is good


async def test_неготовый_поток_повторяется_с_другим_профилем(cog, ctx, monkeypatch):
    """403 от CDN лечится сменой профиля, а не проигрыванием тишины"""
    rotated = []
    monkeypatch.setattr('cogs.music.rotate_stream_client', lambda: rotated.append(1))

    dead = FakeSource('S', ready=False, extractor='youtube')
    alive = FakeSource('S', ready=True, extractor='youtube')
    t = make_track('S', sources=[dead, alive])
    cog.get_queue(ctx.guild.id).append(t)

    await cog._play_next_locked(ctx)

    assert dead.cleaned == 1, 'мёртвый источник не убран'
    assert ctx.voice_client.source is alive
    assert rotated, 'профиль не переключили'


async def test_неготовый_поток_не_крутится_вечно(cog, ctx, monkeypatch):
    """Иначе трек, который не открывается никогда, зациклил бы очередь"""
    monkeypatch.setattr('cogs.music.rotate_stream_client', lambda: None)
    made = []

    async def _resolver(tr, *, loop=None, timeout=30):
        s = FakeSource(tr.title, ready=False, extractor='youtube')
        made.append(s)
        return s

    t = track(title='S', url='https://x/S', duration=200.0)
    t.resolver = _resolver
    cog.get_queue(ctx.guild.id).append(t)

    # без предела попыток цикл бесконечен, и тест повис бы вместо падения
    await asyncio.wait_for(cog._play_next_locked(ctx), timeout=5)

    assert ctx.voice_client.source is None, 'проиграли неоткрывшийся поток'
    assert len(made) <= MAX_PLAY_ATTEMPTS + 1, f'попыток {len(made)}, предел не работает'
    assert all(s.cleaned for s in made)


async def test_ротация_не_трогает_чужие_источники(cog, ctx, monkeypatch):
    """Yandex и SoundCloud идут своими клиентами, профиль YouTube им ни при чём"""
    rotated = []
    monkeypatch.setattr('cogs.music.rotate_stream_client', lambda: rotated.append(1))

    dead = FakeSource('S', ready=False, extractor='')
    alive = FakeSource('S', ready=True, extractor='')
    cog.get_queue(ctx.guild.id).append(make_track('S', sources=[dead, alive]))

    await cog._play_next_locked(ctx)

    assert ctx.voice_client.source is alive
    assert not rotated, 'переключили профиль YouTube на чужом источнике'


# --- находки ревью ------------------------------------------------------------

async def test_repeat_track_не_крутит_мёртвый_трек_вечно(cog, ctx, no_advance):
    """При repeat=track _play_next_locked возвращает текущий трек в голову
    очереди. Если счётчик попыток обнуляется на каждом круге, мёртвый поток
    крутится бесконечно: по сообщению и по extract_info на круг"""
    from cogs.music import MAX_PLAY_ATTEMPTS

    gid = ctx.guild.id
    t = track(title='мёртвый')
    cog._loop_modes[gid] = 'track'
    for _ in range(MAX_PLAY_ATTEMPTS + 1):
        cog._current[gid] = t
        cog.get_queue(gid).clear()
        await cog._advance(ctx, 'FFmpeg exited with code 1')

    assert len(ctx.sent) == 1, 'должно быть ровно одно сообщение о провале'
    assert gid in cog._skip_loop_once, 'повтор не отменён — трек вернётся снова'


async def test_repeat_queue_не_удваивает_трек(cog, ctx, no_advance):
    """Ретрай кладёт трек в голову очереди. Если оставить его в _current,
    _play_next_locked при repeat=queue добавит его ещё и в _played, и трек
    навсегда удвоится в круге"""
    gid = ctx.guild.id
    t = track(title='Песня')
    cog._current[gid] = t
    cog._loop_modes[gid] = 'queue'

    await cog._advance(ctx, 'FFmpeg exited with code 1')

    assert list(cog.get_queue(gid)) == [t]
    assert gid not in cog._current, '_current не очищен: трек попадёт ещё и в _played'
    assert not cog._played.get(gid), 'трек уже уехал в архив круга'


async def test_cog_unload_гасит_таймер_одинокой_гильдии(cog, ctx):
    """Гильдия, где бот только сидит в канале, не попадает ни в queues, ни
    в _prefetched, ни в _np_source. Её таймер пережил бы перезагрузку кога
    и потом отключил бы уже чужую сессию"""
    gid = ctx.guild.id
    cog.queues.pop(gid, None)
    cog._start_alone_timer(gid)
    cog._start_idle_timer(gid)
    alone, idle = cog._alone_tasks[gid], cog._idle_tasks[gid]

    await cog.cog_unload()
    await asyncio.sleep(0)  # отмена проставляется, когда цикл её обработает

    assert alone.done(), 'таймер одиночества пережил выгрузку'
    assert idle.done(), 'таймер простоя пережил выгрузку'
    assert gid not in cog._alone_tasks and gid not in cog._idle_tasks


async def test_исходный_ответ_не_трогают_вовсе(cog, ctx):
    """На исходный ответ ссылаются followup'ы: удаление ломает их, а правка
    портит сообщение, которое пользователь уже прочитал. Карточкой он теперь
    не бывает — команды отвечают текстом до неё, — но страховка остаётся"""
    gid = ctx.guild.id
    msg = types.SimpleNamespace(id=777, edited=[], deleted=0)

    async def _edit(**kw):
        msg.edited.append(kw)

    async def _delete():
        msg.deleted += 1

    msg.edit, msg.delete = _edit, _delete
    cog._original_msgs[msg.id] = None
    cog._np_finished[gid] = (msg, 'Песня — Артист')

    await cog._drop_finished_card(gid)

    assert msg.deleted == 0, 'исходный ответ удалять нельзя'
    assert msg.edited == [], 'исходный ответ править тоже нельзя'


async def test_обычная_карточка_удаляется(cog, ctx):
    """Плейлист на сотню треков иначе оставит сотню мёртвых прогресс-баров"""
    gid = ctx.guild.id
    msg = types.SimpleNamespace(id=888, edited=[], deleted=0)

    async def _edit(**kw):
        msg.edited.append(kw)

    async def _delete():
        msg.deleted += 1

    msg.edit, msg.delete = _edit, _delete
    cog._np_finished[gid] = (msg, 'Песня')

    await cog._drop_finished_card(gid)

    assert msg.deleted == 1
    assert not msg.edited


async def test_сворачивание_переживает_отказ_discord(cog, ctx):
    gid = ctx.guild.id
    msg = types.SimpleNamespace(id=999)

    async def _edit(**kw):
        resp = types.SimpleNamespace(status=404, reason='Not Found')
        raise discord.HTTPException(resp, {'message': 'нет такого', 'code': 10008})

    msg.edit = _edit
    cog._original_msgs[msg.id] = None
    cog._np_finished[gid] = (msg, 'Песня')

    await cog._drop_finished_card(gid)  # не должно бросить


async def test_single_track_card_goes_to_the_channel(cog, ctx, monkeypatch):
    """Иначе заглушку взаимодействия заменит карточка «сейчас играет», станет
    исходным ответом и потеряет право быть удалённой — отсюда брался один
    замороженный прогресс-бар за сессию"""
    t = track(title='Одиночный')

    async def _extract(url, loop=None):
        return 'track', t

    monkeypatch.setattr('cogs.music.extract', _extract)
    monkeypatch.setattr(cog, '_start_if_idle', lambda c: asyncio.sleep(0))
    ctx.voice_client._playing = False  # ничего не играет

    await cog._enqueue(ctx, 'https://x/track', shuffle=False)

    assert ctx.channel.sent, 'карточка добавления не ушла в канал'
    assert ctx.sent == [], 'ответом команды карточка теряет обложку'
    assert list(cog.get_queue(ctx.guild.id)) == [t]


async def test_card_goes_to_the_channel_while_playing(cog, ctx, monkeypatch):
    """Поведение не должно зависеть от того, играет ли что-то"""
    t = track(title='Второй')

    async def _extract(url, loop=None):
        return 'track', t

    monkeypatch.setattr('cogs.music.extract', _extract)
    monkeypatch.setattr(cog, '_start_if_idle', lambda c: asyncio.sleep(0))
    ctx.voice_client._playing = True

    await cog._enqueue(ctx, 'https://x/track', shuffle=False)

    assert ctx.channel.sent
    assert ctx.sent == []


async def test_slash_card_is_a_followup(cog, ctx, monkeypatch):
    """У слэш-команды над followup-ом Discord рисует «использует /play»

    Правкой исходного ответа карточку слать нельзя, она теряет обложку.
    Удалять исходный ответ тоже нельзя: первый followup после дефёра сам
    занимает его слот, и удаление стирало саму карточку
    """
    from conftest import SlottedInteraction

    t = track(title='Слэш')

    async def _extract(url, loop=None):
        return 'track', t

    monkeypatch.setattr('cogs.music.extract', _extract)
    monkeypatch.setattr(cog, '_start_if_idle', lambda c: asyncio.sleep(0))
    inter = SlottedInteraction(iid=7)
    ctx.interaction = inter

    await cog._enqueue(ctx, 'https://x/track', shuffle=False)

    assert ctx.sent and ctx.sent[-1].embed is not None, 'карточка не ушла followup-ом'
    assert ctx.sent[-1].ephemeral is False, 'карточку добавления должны видеть все'
    assert ctx.channel.sent == [], 'мимо взаимодействия заголовок команды теряется'
    assert inter.edited == [], 'правка исходного ответа съедает обложку'
    assert not inter.dismissed, 'followup занял слот заглушки — удаление сотрёт карточку'
    assert inter.id in cog._answered, 'иначе следующий _respond затрёт карточку правкой'
    assert ctx.sent[-1].id in cog._original_msgs, 'карточку могут удалить как чужую'


# --- добавление сразу после текущего ------------------------------------------

async def test_playnext_ставит_трек_в_голову(cog, ctx, monkeypatch):
    """Смысл команды: заиграть следующим, а не в конце очереди"""
    было = [track(title='Старый1'), track(title='Старый2')]
    cog.get_queue(ctx.guild.id).extend(было)
    новый = track(title='Срочный')

    async def _extract(url, loop=None):
        return 'track', новый

    monkeypatch.setattr('cogs.music.extract', _extract)
    monkeypatch.setattr(cog, '_start_if_idle', lambda c: asyncio.sleep(0))

    await cog._enqueue(ctx, 'https://x/track', shuffle=False, front=True)

    assert list(cog.get_queue(ctx.guild.id)) == [новый, *было]


async def test_playnext_сохраняет_порядок_плейлиста(cog, ctx, monkeypatch):
    """extendleft разворачивает порядок — если про это забыть, плейлист
    встанет задом наперёд"""
    хвост = track(title='Хвост')
    cog.get_queue(ctx.guild.id).append(хвост)
    треки = [track(title=f'#{i}') for i in range(4)]
    payload = types.SimpleNamespace(tracks=треки, kind='playlist', title='Альбом',
                                    url='https://x/pl', artist='A', thumbnail='')

    async def _extract(url, loop=None):
        return 'playlist', payload

    monkeypatch.setattr('cogs.music.extract', _extract)
    monkeypatch.setattr(cog, '_start_if_idle', lambda c: asyncio.sleep(0))

    await cog._enqueue(ctx, 'https://x/pl', shuffle=False, front=True)

    assert list(cog.get_queue(ctx.guild.id)) == [*треки, хвост]


async def test_обычное_добавление_идёт_в_конец(cog, ctx, monkeypatch):
    """Страховка: playnext не должен поменять поведение play"""
    старый = track(title='Старый')
    cog.get_queue(ctx.guild.id).append(старый)
    новый = track(title='Новый')

    async def _extract(url, loop=None):
        return 'track', новый

    monkeypatch.setattr('cogs.music.extract', _extract)
    monkeypatch.setattr(cog, '_start_if_idle', lambda c: asyncio.sleep(0))

    await cog._enqueue(ctx, 'https://x/track', shuffle=False)

    assert list(cog.get_queue(ctx.guild.id)) == [старый, новый]


# --- карточка при остановке ---------------------------------------------------

async def test_stop_переводит_карточку_в_остановленную(cog, ctx):
    """Иначе карточка остаётся замороженной с кнопкой «играет»: пользователь
    видит ▶ у того, что уже не играет"""
    gid = ctx.guild.id
    cog.get_queue(gid).append(make_track('A'))
    await cog._play_next_locked(ctx)
    card = cog._np_msg[gid]
    edits_before = len(card.edits)

    await cog.stop.callback(cog, ctx)

    assert len(card.edits) > edits_before, 'карточку не обновили при остановке'
    embed = card.edits[-1].get('embed')
    assert embed is not None and '⏹' in embed.description, 'состояние не остановленное'


async def test_карточка_финализируется_любым_способом_завершения(cog, ctx):
    """Сброс сессии общий для стопа, выхода и таймаутов — финализация обязана
    жить в нём, а не в каждой команде отдельно"""
    gid = ctx.guild.id
    cog.get_queue(gid).append(make_track('A'))
    await cog._play_next_locked(ctx)
    card = cog._np_msg[gid]
    edits_before = len(card.edits)

    await cog._reset_session(gid)

    assert len(card.edits) > edits_before, 'сброс сессии не финализировал карточку'


# --- заготовка при смене головы очереди ---------------------------------------

def prefetch_head(cog, gid):
    """Трек, про который сейчас заготовка: готовая либо ещё в полёте"""
    ready = cog._prefetched.get(gid)
    if ready is not None:
        return ready[0]
    pending = cog._prefetching.get(gid)
    return pending[0] if pending is not None else None


async def test_playnext_starts_prefetch_for_new_head(cog, ctx, monkeypatch):
    """Иначе вставленный вперёд трек резолвится с нуля, и его приходится ждать"""
    gid = ctx.guild.id
    cog.get_queue(gid).append(make_track('Старый'))
    fresh = make_track('Срочный')

    async def _extract(url, loop=None):
        return 'track', fresh

    monkeypatch.setattr('cogs.music.extract', _extract)
    monkeypatch.setattr(cog, '_start_if_idle', lambda c: asyncio.sleep(0))
    ctx.voice_client._playing = True

    await cog._enqueue(ctx, 'https://x/track', shuffle=False, front=True)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert prefetch_head(cog, gid) is fresh, 'заготовка про старую голову очереди'


async def test_append_to_empty_queue_is_prefetched(cog, ctx, monkeypatch):
    """Играет трек, очередь пуста: заготовки не было вовсе, и добавленный
    в конец становится головой"""
    gid = ctx.guild.id
    fresh = make_track('Следующий')

    async def _extract(url, loop=None):
        return 'track', fresh

    monkeypatch.setattr('cogs.music.extract', _extract)
    monkeypatch.setattr(cog, '_start_if_idle', lambda c: asyncio.sleep(0))
    ctx.voice_client._playing = True

    await cog._enqueue(ctx, 'https://x/track', shuffle=False)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert prefetch_head(cog, gid) is fresh


async def test_append_keeps_valid_prefetch(cog, ctx, monkeypatch):
    """Голова не изменилась — переделывать нечего, иначе жгли бы запросы зря"""
    gid = ctx.guild.id
    head = make_track('Голова')
    cog.get_queue(gid).append(head)
    source = FakeSource('Голова')
    cog._prefetched[gid] = (head, source)

    async def _extract(url, loop=None):
        return 'track', make_track('Хвост')

    monkeypatch.setattr('cogs.music.extract', _extract)
    monkeypatch.setattr(cog, '_start_if_idle', lambda c: asyncio.sleep(0))
    ctx.voice_client._playing = True

    await cog._enqueue(ctx, 'https://x/track', shuffle=False)

    assert cog._prefetched.get(gid) == (head, source), 'сбросили годную заготовку'
    assert source.cleaned == 0


async def test_idle_does_not_prefetch(cog, ctx, monkeypatch):
    """Пока ничего не играет, трек возьмёт _play_next_locked напрямую"""
    async def _extract(url, loop=None):
        return 'track', make_track('Первый')

    monkeypatch.setattr('cogs.music.extract', _extract)
    monkeypatch.setattr(cog, '_start_if_idle', lambda c: asyncio.sleep(0))
    ctx.voice_client._playing = False

    await cog._enqueue(ctx, 'https://x/track', shuffle=False)
    await asyncio.sleep(0)

    assert ctx.guild.id not in cog._prefetched
    assert ctx.guild.id not in cog._prefetching


async def test_shuffle_rebuilds_prefetch(cog, ctx):
    """После перемешивания заготовка почти наверняка про чужой трек: её надо
    снять и запустить новую, иначе первый трек после /shuffle придётся ждать"""
    gid = ctx.guild.id
    tracks = [make_track(f'#{i}') for i in range(8)]
    cog.get_queue(gid).extend(tracks)
    stale = FakeSource('старая')
    cog._prefetched[gid] = (tracks[0], stale)
    ctx.voice_client._playing = True

    await cog.shuffle.callback(cog, ctx)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    head = cog.get_queue(gid)[0]
    if head is tracks[0]:
        return  # перемешивание оставило ту же голову — заготовка годная
    assert stale.cleaned == 1, 'чужую заготовку не убрали'
    assert prefetch_head(cog, gid) is head


async def test_card_delete_failure_is_logged(cog, ctx, monkeypatch):
    """Отказ удаления оставляет мёртвый прогресс-бар навсегда

    Молчаливый except прятал этот баг: карточка на границе 15-минутного окна
    не удалялась, и увидеть это можно было только глазами в канале
    """
    import cogs.music as music

    written: list[str] = []
    monkeypatch.setattr(music, 'session_log', lambda gid, msg: written.append(msg))

    gid = ctx.guild.id
    cog.get_queue(gid).extend([make_track('A'), make_track('B')])
    await cog._play_next_locked(ctx)

    card = cog._np_msg[gid]

    async def _refuse():
        raise discord.HTTPException(
            types.SimpleNamespace(status=401, reason='Unauthorized'), 'expired webhook')

    card.delete = _refuse

    ctx.voice_client._playing = False
    await cog._play_next_locked(ctx)

    assert any('не удалилась' in m for m in written), \
        f'отказ удаления должен попасть в журнал, а там: {written}'
