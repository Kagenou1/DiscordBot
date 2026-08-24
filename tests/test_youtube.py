"""YouTube и SoundCloud: разбор записей yt-dlp, обложки плейлистов"""
import sys

import pytest

from audio.soundcloud import parse as sc_parse
from audio.youtube import parse as yt_parse
from audio.youtube import playlist as yt_playlist


def test_ytm_regexes():
    assert yt_parse.YTM_PLAYLIST_RE.search(
        'https://music.youtube.com/playlist?list=PL_abc-1').group(1) == 'PL_abc-1'
    assert yt_parse.YTM_WATCH_RE.search(
        'https://music.youtube.com/watch?v=lYBUbBu4W08&si=x').group(1) == 'lYBUbBu4W08'


@pytest.mark.parametrize('given,expected', [
    ('https://www.youtube.com/watch?v=abc',
     'https://music.youtube.com/watch?v=abc'),
    ('https://youtube.com/watch?v=abc',
     'https://music.youtube.com/watch?v=abc'),
    ('https://m.youtube.com/watch?v=abc',
     'https://music.youtube.com/watch?v=abc'),
    ('http://www.youtube.com/watch?v=abc',
     'https://music.youtube.com/watch?v=abc'),
    # уже музыкальный хост не трогаем
    ('https://music.youtube.com/watch?v=abc',
     'https://music.youtube.com/watch?v=abc'),
    # короткая форма и чужие домены не наши
    ('https://youtu.be/abc', 'https://youtu.be/abc'),
    ('https://open.spotify.com/track/x', 'https://open.spotify.com/track/x'),
    ('', ''),
])
def test_as_music_url(given, expected):
    """yt-dlp нормализует music.youtube.com в www, и ссылка в карточке уводила
    с YT Music на YouTube, а подпись источника показывала не тот сервис"""
    assert yt_parse.as_music_url(given) == expected


async def test_extract_keeps_music_host(monkeypatch):
    """Вставили ссылку YT Music — карточка обязана вести туда же"""
    # именно из sys.modules: пакет переэкспортирует функцию extract, и обычный
    # import связал бы имя с ней, а не с модулем
    import audio.youtube.extract  # noqa: F401
    yt_extract = sys.modules['audio.youtube.extract']

    data = {'webpage_url': 'https://www.youtube.com/watch?v=r_hJWTufE6w',
            'title': 'SPECIALZ', 'uploader': 'King Gnu - Topic', 'duration': 234}
    monkeypatch.setattr(yt_extract, 'extract_info', lambda url: data)
    monkeypatch.setattr(yt_extract, 'ytm_square_thumbnail', lambda vid: 'https://cover/sq')

    kind, track = await yt_extract.extract(
        'https://music.youtube.com/watch?v=r_hJWTufE6w')

    assert kind == 'track'
    assert track.url == 'https://music.youtube.com/watch?v=r_hJWTufE6w'
    assert track.thumbnail == 'https://cover/sq', 'квадратную обложку тоже дотягиваем'


async def test_extract_leaves_plain_youtube_alone(monkeypatch):
    """Обычную ссылку YouTube в YT Music переписывать нельзя"""
    # именно из sys.modules: пакет переэкспортирует функцию extract, и обычный
    # import связал бы имя с ней, а не с модулем
    import audio.youtube.extract  # noqa: F401
    yt_extract = sys.modules['audio.youtube.extract']

    data = {'webpage_url': 'https://www.youtube.com/watch?v=r_hJWTufE6w',
            'title': 'SPECIALZ', 'duration': 234}
    monkeypatch.setattr(yt_extract, 'extract_info', lambda url: data)

    kind, track = await yt_extract.extract('https://www.youtube.com/watch?v=r_hJWTufE6w')

    assert track.url == 'https://www.youtube.com/watch?v=r_hJWTufE6w'


def test_entry_to_track_strips_topic_suffix():
    t = yt_parse.entry_to_track({'webpage_url': 'u', 'title': 'S', 'uploader': 'Artist - Topic'}, resolver=None)
    assert t.artist == 'Artist'


def test_entry_to_track_prefers_artists_list():
    t = yt_parse.entry_to_track(
        {'url': 'u', 'title': 'S', 'uploader': 'Chan', 'artists': ['A1', 'A2']}, resolver=None)
    assert t.artist == 'A1, A2'


def test_entry_to_track_populates_duration():
    t = yt_parse.entry_to_track({'url': 'u', 'title': 'S', 'duration': 245.5}, resolver=None)
    assert t.duration == pytest.approx(245.5)


@pytest.mark.parametrize('bad', [None, {'title': 'нет url'}])
def test_entry_to_track_rejects_unusable(bad):
    assert yt_parse.entry_to_track(bad, resolver=None) is None


def test_entry_to_track_survives_bad_duration():
    t = yt_parse.entry_to_track({'url': 'u', 'title': 'S', 'duration': 'нет'}, resolver=None)
    assert t.duration == 0.0


def test_entry_to_track_falls_back_to_thumbnails_list():
    t = yt_parse.entry_to_track(
        {'url': 'u', 'title': 'S', 'thumbnails': [{'url': 'small'}, {'url': 'big'}]}, resolver=None)
    assert t.thumbnail == 'big'


def test_playlist_thumbnail_rejects_auto_collage():
    tracks = [yt_parse.entry_to_track({'url': 'u', 'title': 'S', 'thumbnail': 'track-cover'}, resolver=None)]
    auto = [{'url': 'https://yt3.googleusercontent.com/abc=s576'}]
    assert yt_playlist._pick_playlist_thumbnail(auto, tracks) == 'track-cover'


def test_playlist_thumbnail_keeps_real_cover():
    tracks = [yt_parse.entry_to_track({'url': 'u', 'title': 'S', 'thumbnail': 'track-cover'}, resolver=None)]
    real = [{'url': 'https://i.ytimg.com/vi/x/maxres.jpg'}]
    assert yt_playlist._pick_playlist_thumbnail(real, tracks) == 'https://i.ytimg.com/vi/x/maxres.jpg'


def test_playlist_thumbnail_without_tracks():
    assert yt_playlist._pick_playlist_thumbnail([], []) == ''


# --- SoundCloud -------------------------------------------------------------

def test_sc_set_regex():
    assert sc_parse.SC_SET_RE.search('https://soundcloud.com/user/sets/my-set') is not None
    assert sc_parse.SC_SET_RE.search('https://soundcloud.com/user/single-track') is None


def test_sc_entry_to_track():
    t = sc_parse.entry_to_track(
        {'webpage_url': 'https://soundcloud.com/u/t', 'title': 'S', 'uploader': 'U', 'duration': 90},
        resolver=None)
    assert (t.title, t.artist, t.duration) == ('S', 'U', 90.0)


def test_sc_entry_to_track_default_title():
    t = sc_parse.entry_to_track({'url': 'u'}, resolver=None)
    assert t.title == 'Без названия'
