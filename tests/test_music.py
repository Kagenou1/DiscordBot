"""Логика кога: разбор времени, очередь, локи, кэш Track"""
import asyncio
import time

import pytest

from conftest import FakeCtx, FakeGuild, FakeVoiceClient, track
from cogs.music import _parse_time_position


@pytest.mark.parametrize('text,expected', [
    ('90', 90.0), ('0', 0.0), ('1:30', 90.0), ('01:30', 90.0),
    ('1:02:03', 3723.0), ('  45  ', 45.0), ('2:00.5', 120.5),
])
def test_parse_time_position_valid(text, expected):
    assert _parse_time_position(text) == pytest.approx(expected)


@pytest.mark.parametrize('text', ['abc', '', '1:2:3:4', 'a:b', '1:xx'])
def test_parse_time_position_invalid(text):
    assert _parse_time_position(text) is None


@pytest.mark.parametrize('text', ['nan', 'inf', '-inf', 'NaN', 'Infinity'])
def test_parse_time_position_rejects_non_finite(text):
    """nan проходил проверку target < 0 и уезжал в ffmpeg-флаг -ss"""
    assert _parse_time_position(text) is None


# --- очередь ----------------------------------------------------------------

def test_get_queue_is_per_guild(cog):
    cog.get_queue(1).append(track())
    assert len(cog.get_queue(1)) == 1
    assert len(cog.get_queue(2)) == 0


def test_drop_from_queue_discards_by_default(cog):
    cog.get_queue(1).extend(track(title=f'S{i}') for i in range(5))
    assert cog._drop_from_queue(1, 3) == 3
    assert len(cog.get_queue(1)) == 2
    assert cog._played.get(1) is None


def test_drop_from_queue_keeps_tracks_in_queue_loop(cog):
    """В режиме repeat queue пропущенные треки должны остаться в цикле"""
    cog._loop_modes[1] = 'queue'
    cog.get_queue(1).extend(track(title=f'S{i}') for i in range(5))
    cog._drop_from_queue(1, 3)
    assert [t.title for t in cog._played[1]] == ['S0', 'S1', 'S2']


def test_drop_from_queue_clamps_to_length(cog):
    cog.get_queue(1).extend(track() for _ in range(2))
    assert cog._drop_from_queue(1, 99) == 2
    assert len(cog.get_queue(1)) == 0


# --- локи -------------------------------------------------------------------

def test_play_lock_is_stable_per_guild(cog):
    assert cog._play_lock(1) is cog._play_lock(1)
    assert cog._play_lock(1) is not cog._play_lock(2)


async def test_start_if_idle_skips_when_playing(cog, monkeypatch):
    called = []
    monkeypatch.setattr(cog, '_play_next_locked', lambda ctx: called.append(1))
    ctx = FakeCtx(voice_client=FakeVoiceClient(playing=True))
    await cog._start_if_idle(ctx)
    assert called == []


async def test_start_if_idle_skips_when_paused(cog, monkeypatch):
    called = []
    monkeypatch.setattr(cog, '_play_next_locked', lambda ctx: called.append(1))
    await cog._start_if_idle(FakeCtx(voice_client=FakeVoiceClient(paused=True)))
    assert called == []


async def test_start_if_idle_runs_when_idle(cog):
    calls = []

    async def _fake(ctx):
        calls.append(ctx)

    cog._play_next_locked = _fake
    await cog._start_if_idle(FakeCtx(voice_client=FakeVoiceClient()))
    assert len(calls) == 1


async def test_play_next_serialises_concurrent_calls(cog):
    """Без лока /skip и /play могли запустить два AudioPlayer одновременно"""
    active = 0
    peak = 0

    async def _fake(ctx):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1

    cog._play_next_locked = _fake
    ctx = FakeCtx(guild=FakeGuild(gid=7))
    await asyncio.gather(*(cog._play_next(ctx) for _ in range(5)))
    assert peak == 1


async def test_play_locks_are_independent_across_guilds(cog):
    order = []

    async def _fake(ctx):
        order.append(ctx.guild.id)
        await asyncio.sleep(0.02)

    cog._play_next_locked = _fake
    await asyncio.gather(
        cog._play_next(FakeCtx(guild=FakeGuild(gid=1))),
        cog._play_next(FakeCtx(guild=FakeGuild(gid=2))),
    )
    assert sorted(order) == [1, 2]


# --- учёт времени -----------------------------------------------------------

def test_elapsed_without_start(cog):
    assert cog._elapsed(1) == 0.0


def test_elapsed_subtracts_pause(cog):
    now = time.monotonic()
    cog._track_start[1] = now - 100
    cog._pause_total[1] = 30
    assert cog._elapsed(1) == pytest.approx(70, abs=0.5)


def test_elapsed_accounts_for_open_pause(cog):
    now = time.monotonic()
    cog._track_start[1] = now - 100
    cog._pause_start[1] = now - 20
    assert cog._elapsed(1) == pytest.approx(80, abs=0.5)


def test_elapsed_never_negative(cog):
    cog._track_start[1] = time.monotonic()
    cog._pause_total[1] = 500
    assert cog._elapsed(1) == 0.0


# --- фоновые задачи ---------------------------------------------------------

async def test_spawn_keeps_reference_until_done(cog):
    async def _work():
        await asyncio.sleep(0.01)

    task = cog._spawn(_work())
    assert task in cog._bg_tasks
    await task
    await asyncio.sleep(0)
    assert task not in cog._bg_tasks


# --- кэш Track --------------------------------------------------------------

async def test_track_cache_is_single_use(monkeypatch):
    from audio import track as track_mod

    t = track()
    built = []

    class FakeSource:
        @classmethod
        def from_resolved(cls, data, *, start=0.0):
            built.append(data)
            return 'SOURCE'

    monkeypatch.setattr('audio.source.OpusAudioSource', FakeSource)
    t.cache_resolved({'url': 'stream'})
    assert await t.make_source() == 'SOURCE'
    assert t._resolved is None  # кэш одноразовый

    calls = []

    async def _resolver(tr, *, loop=None, timeout=30):
        calls.append(tr)
        return 'FRESH'

    t.resolver = _resolver
    assert await t.make_source() == 'FRESH'
    assert len(calls) == 1


async def test_track_cache_expires(monkeypatch):
    from audio import track as track_mod

    t = track()
    t.cache_resolved({'url': 'stream'})
    t._resolved_at = time.monotonic() - track_mod._RESOLVED_TTL - 1

    async def _resolver(tr, *, loop=None, timeout=30):
        return 'FRESH'

    t.resolver = _resolver
    assert await t.make_source() == 'FRESH'


async def test_track_without_resolver_raises():
    t = track()
    with pytest.raises(RuntimeError):
        await t.make_source()
