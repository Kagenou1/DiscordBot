"""Spotify: регексы ссылок, разбор элементов, постраничная загрузка"""
import pytest

from audio.spotify import parse, playlist


TRACK_ID = '4cOdK2wGLETKBW3PvgPWqT'


@pytest.mark.parametrize('url', [
    f'https://open.spotify.com/track/{TRACK_ID}',
    f'https://open.spotify.com/track/{TRACK_ID}?si=xyz',
    f'https://open.spotify.com/intl-ru/track/{TRACK_ID}',
    f'https://open.spotify.com/intl-pt-br/track/{TRACK_ID}?si=xyz',
])
def test_track_regex_accepts_locale_prefix(url):
    m = parse.SPOTIFY_TRACK_RE.search(url)
    assert m and m.group(1) == TRACK_ID


def test_album_and_playlist_regexes():
    assert parse.SPOTIFY_ALBUM_RE.search('https://open.spotify.com/intl-de/album/A1').group(1) == 'A1'
    assert parse.SPOTIFY_PLAYLIST_RE.search('https://open.spotify.com/playlist/P1').group(1) == 'P1'


def test_regexes_do_not_cross_match():
    assert parse.SPOTIFY_TRACK_RE.search('https://open.spotify.com/album/A1') is None
    assert parse.SPOTIFY_ALBUM_RE.search('https://open.spotify.com/track/T1') is None


@pytest.mark.parametrize('entry,expected', [
    ({'item': {'name': 'X'}}, 'X'),
    ({'track': {'name': 'Y'}}, 'Y'),
    ({'item': None, 'track': {'name': 'Z'}}, 'Z'),
    ({}, None),
    (None, None),
])
def test_playlist_entry_item_accepts_both_keys(entry, expected):
    """API отдаёт item, документация обещает track — читаем оба"""
    got = parse.playlist_entry_item(entry)
    assert (got or {}).get('name') if got else got is None
    assert (got['name'] if got else None) == expected


def test_item_to_track_extracts_fields():
    item = {
        'name': 'Song',
        'artists': [{'name': 'A1'}, {'name': 'A2'}],
        'external_urls': {'spotify': 'https://open.spotify.com/track/T'},
        'album': {'images': [{'url': 'https://img/big.jpg'}]},
        'duration_ms': 245000,
    }
    t = parse.item_to_track(item, resolver=None)
    assert t.title == 'Song'
    assert t.artist == 'A1, A2'
    assert t.url == 'https://open.spotify.com/track/T'
    assert t.thumbnail == 'https://img/big.jpg'
    assert t.duration == pytest.approx(245.0)


def test_item_to_track_rejects_empty():
    assert parse.item_to_track(None, resolver=None) is None
    assert parse.item_to_track({}, resolver=None) is None
    assert parse.item_to_track({'name': ''}, resolver=None) is None


class FakeSpotify:
    """Заглушка spotipy с постраничной выдачей и счётчиком вызовов"""

    def __init__(self, total):
        self.total = total
        self.calls = []

    def playlist(self, pid, fields=None):
        self.calls.append(('playlist', pid))
        return {'name': 'PL', 'external_urls': {'spotify': 'u'}, 'images': [{'url': 'cover'}]}

    def playlist_items(self, pid, limit=100, offset=0, additional_types=None):
        self.calls.append(('items', offset))
        end = min(offset + limit, self.total)
        items = [
            {'item': {'name': f'S{i}', 'artists': [{'name': 'A'}], 'duration_ms': 1000,
                      'external_urls': {'spotify': f'url{i}'}, 'album': {'images': []}}}
            for i in range(offset, end)
        ]
        return {'total': self.total, 'items': items}

    def album(self, aid):
        self.calls.append(('album', aid))
        items = [{'name': f'T{i}', 'artists': [{'name': 'A'}], 'duration_ms': 1000,
                  'external_urls': {}, } for i in range(min(50, self.total))]
        return {'name': 'AL', 'external_urls': {'spotify': 'au'}, 'images': [{'url': 'ac'}],
                'tracks': {'items': items, 'total': self.total}}

    def album_tracks(self, aid, limit=50, offset=0):
        self.calls.append(('album_tracks', offset))
        end = min(offset + limit, self.total)
        return {'items': [{'name': f'T{i}', 'artists': [{'name': 'A'}], 'duration_ms': 1000,
                           'external_urls': {}} for i in range(offset, end)]}


def test_playlist_paginates_sequentially(monkeypatch):
    fake = FakeSpotify(total=250)
    monkeypatch.setattr(playlist, 'sp', fake)
    info = playlist._load_playlist('P', resolver=None, limit=None)
    assert len(info.tracks) == 250
    assert info.title == 'PL'
    assert info.thumbnail == 'cover'
    # первая страница плюс две добравшие, без параллелизма
    assert [c for c in fake.calls if c[0] == 'items'] == [('items', 0), ('items', 100), ('items', 200)]


def test_playlist_respects_limit(monkeypatch):
    monkeypatch.setattr(playlist, 'sp', FakeSpotify(total=250))
    info = playlist._load_playlist('P', resolver=None, limit=7)
    assert len(info.tracks) == 7


def test_playlist_falls_back_to_first_track_cover(monkeypatch):
    fake = FakeSpotify(total=2)
    fake.playlist = lambda pid, fields=None: {'name': 'PL', 'external_urls': {}, 'images': []}
    monkeypatch.setattr(playlist, 'sp', fake)
    info = playlist._load_playlist('P', resolver=None, limit=None)
    assert info.thumbnail == info.tracks[0].thumbnail


def test_album_uses_embedded_page(monkeypatch):
    fake = FakeSpotify(total=60)
    monkeypatch.setattr(playlist, 'sp', fake)
    info = playlist._load_album('A', resolver=None, limit=None)
    assert info.kind == 'album'
    assert len(info.tracks) == 60
    # первые 50 приходят внутри sp.album, докачивается одна страница
    assert [c for c in fake.calls if c[0] == 'album_tracks'] == [('album_tracks', 50)]
    # у простых треков альбома нет обложки — подставляется обложка альбома
    assert all(t.thumbnail == 'ac' for t in info.tracks)


def test_disabled_client_raises(monkeypatch):
    monkeypatch.setattr(playlist, 'sp', None)
    with pytest.raises(RuntimeError):
        playlist._load_playlist('P', resolver=None, limit=None)
