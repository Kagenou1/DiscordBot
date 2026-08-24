"""Yandex Music: регексы, обложки, разбор треков, пакетная загрузка плейлиста"""
import types

import pytest

import importlib

from audio.yandex import parse, playlist

# audio.yandex.resolve как атрибут — это функция из __init__, нужен именно модуль
ya_resolve = importlib.import_module('audio.yandex.resolve')


def test_track_regex_with_and_without_album():
    assert parse.YA_TRACK_RE.search('https://music.yandex.ru/album/12/track/34').group(1) == '34'
    assert parse.YA_TRACK_RE.search('https://music.yandex.com/track/34').group(1) == '34'


def test_playlist_regexes():
    m = parse.YA_PLAYLIST_RE.search('https://music.yandex.ru/users/vasya/playlists/1003')
    assert m.groups() == ('vasya', '1003')
    m = parse.YA_PLAYLIST_UUID_RE.search('https://music.yandex.ru/playlists/ab-cd-12')
    assert m.group(1) == 'ab-cd-12'


def test_uuid_regex_does_not_swallow_user_playlists():
    """Порядок проверок в extract зависит от того, что uuid-регекс сюда не лезет"""
    assert parse.YA_PLAYLIST_UUID_RE.search('https://music.yandex.ru/users/u/playlists/12') is None


@pytest.mark.parametrize('uri,expected', [
    ('avatars.yandex.net/get-music/1/%%', 'https://avatars.yandex.net/get-music/1/400x400'),
    ('https://avatars.yandex.net/x.jpg', 'https://avatars.yandex.net/x.jpg'),
    (None, ''),
    ('', ''),
])
def test_cover_url(uri, expected):
    assert parse.cover_url(uri) == expected


def _ya_track(tid='7', title='S', available=True, albums=(11,)):
    return types.SimpleNamespace(
        id=tid, title=title, available=available,
        artists=[types.SimpleNamespace(name='A')],
        albums=[types.SimpleNamespace(id=a) for a in albums],
        cover_uri='avatars.yandex.net/c/%%',
        duration_ms=180000,
    )


def test_ya_track_to_track_builds_album_url():
    t = parse.ya_track_to_track(_ya_track(), resolver=None)
    assert t.url == 'https://music.yandex.ru/album/11/track/7'
    assert t.artist == 'A'
    assert t.thumbnail.endswith('400x400')


def test_ya_track_to_track_skips_unavailable():
    assert parse.ya_track_to_track(_ya_track(available=False), resolver=None) is None
    assert parse.ya_track_to_track(None, resolver=None) is None


class FakeYaClient:
    """Считает вызовы tracks(), чтобы проверить пакетную загрузку"""

    def __init__(self):
        self.batches = []

    def tracks(self, ids):
        self.batches.append(list(ids))
        return [_ya_track(tid=str(i).split(':')[0]) for i in ids]


def _short(tid, inline=False):
    return types.SimpleNamespace(
        id=str(tid),
        track_id=f'{tid}:100',
        track=_ya_track(tid=str(tid)) if inline else None,
    )


def test_materialize_batches_missing_tracks(monkeypatch):
    fake = FakeYaClient()
    monkeypatch.setattr(playlist, 'yandex_client', fake)
    pl = types.SimpleNamespace(tracks=[_short(i) for i in range(250)])
    tracks = playlist._materialize(pl, resolver=None, limit=None)
    assert len(tracks) == 250
    # 250 треков одним запросом на сотню, а не 250 отдельных
    assert [len(b) for b in fake.batches] == [100, 100, 50]


def test_materialize_skips_inline_tracks(monkeypatch):
    fake = FakeYaClient()
    monkeypatch.setattr(playlist, 'yandex_client', fake)
    pl = types.SimpleNamespace(tracks=[_short(i, inline=True) for i in range(10)])
    tracks = playlist._materialize(pl, resolver=None, limit=None)
    assert len(tracks) == 10
    assert fake.batches == []  # ничего не дозапрашивали


def test_materialize_limit_cuts_before_fetching(monkeypatch):
    fake = FakeYaClient()
    monkeypatch.setattr(playlist, 'yandex_client', fake)
    pl = types.SimpleNamespace(tracks=[_short(i) for i in range(500)])
    tracks = playlist._materialize(pl, resolver=None, limit=5)
    assert len(tracks) == 5
    assert [len(b) for b in fake.batches] == [5]


def test_materialize_survives_batch_failure(monkeypatch):
    class Broken(FakeYaClient):
        def tracks(self, ids):
            raise RuntimeError('boom')

    monkeypatch.setattr(playlist, 'yandex_client', Broken())
    pl = types.SimpleNamespace(tracks=[_short(i) for i in range(3)])
    assert playlist._materialize(pl, resolver=None, limit=None) == []


def test_codec_ranking_prefers_flac_then_bitrate():
    def di(codec, br):
        return types.SimpleNamespace(codec=codec, bitrate_in_kbps=br)

    variants = [di('mp3', 320), di('aac', 256), di('flac', 0), di('he-aac', 64)]
    assert max(variants, key=ya_resolve._rank).codec == 'flac'
    only_lossy = [di('mp3', 192), di('mp3', 320), di('aac', 256)]
    best = max(only_lossy, key=ya_resolve._rank)
    assert (best.codec, best.bitrate_in_kbps) == ('mp3', 320)


def test_playlist_meta_ignores_mosaic_cover():
    pl = types.SimpleNamespace(
        title='PL', cover=types.SimpleNamespace(type='mosaic', uri='avatars/x/%%'), og_image=None,
    )
    tracks = [parse.ya_track_to_track(_ya_track(), resolver=None)]
    title, url, thumb = playlist._playlist_meta(pl, fallback_url='u', tracks=tracks)
    assert title == 'PL'
    assert thumb == tracks[0].thumbnail  # коллаж отброшен, взята обложка первого трека
