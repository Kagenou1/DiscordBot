"""Диспетчер audio.extract: домен ссылки -> провайдер"""
import pytest

import audio


@pytest.fixture
def spy(monkeypatch):
    """Подменяет extract у всех провайдеров, возвращает имя сработавшего"""
    calls = []

    def make(name):
        async def _fake(url, *, loop=None, timeout=30):
            calls.append((name, url))
            return 'track', None
        return _fake

    monkeypatch.setattr(audio._spotify, 'extract', make('spotify'))
    monkeypatch.setattr(audio._yandex, 'extract', make('yandex'))
    monkeypatch.setattr(audio._soundcloud, 'extract', make('soundcloud'))
    monkeypatch.setattr(audio._youtube, 'extract', make('youtube'))
    return calls


@pytest.mark.parametrize('url,expected', [
    ('https://open.spotify.com/track/abc', 'spotify'),
    ('https://open.spotify.com/intl-ru/playlist/abc', 'spotify'),
    ('https://music.yandex.ru/album/1/track/2', 'yandex'),
    ('https://music.yandex.by/users/u/playlists/3', 'yandex'),
    ('https://music.yandex.kz/playlists/uuid-1', 'yandex'),
    ('https://music.yandex.com/album/9', 'yandex'),
    ('https://soundcloud.com/user/track', 'soundcloud'),
    ('https://on.soundcloud.com/abc', 'soundcloud'),
    ('https://m.soundcloud.com/user/sets/x', 'soundcloud'),
    ('https://www.youtube.com/watch?v=abc', 'youtube'),
    ('https://music.youtube.com/playlist?list=X', 'youtube'),
    ('https://youtu.be/abc', 'youtube'),
    ('просто поисковый запрос', 'youtube'),
])
async def test_routes_to_expected_provider(spy, url, expected):
    await audio.extract(url)
    assert spy == [(expected, url)]


async def test_non_string_falls_back_to_youtube(spy):
    await audio.extract(None)
    assert spy[0][0] == 'youtube'


async def test_timeout_and_loop_are_forwarded(monkeypatch):
    seen = {}

    async def _fake(url, *, loop=None, timeout=30):
        seen.update(url=url, loop=loop, timeout=timeout)
        return 'track', None

    monkeypatch.setattr(audio._youtube, 'extract', _fake)
    await audio.extract('q', loop='LOOP', timeout=5)
    assert seen == {'url': 'q', 'loop': 'LOOP', 'timeout': 5}
