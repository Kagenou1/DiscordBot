"""Живые проверки всех четырёх сервисов

Запуск: pytest -m network
Требуют сети и ключей в private.py; при отсутствии клиента тест пропускается
"""
import json

import pytest

import audio
from audio.soundcloud.client import sc_ytdl
from audio.spotify.client import sp
from audio.yandex.client import yandex_client
from audio.youtube.client import ytm

pytestmark = pytest.mark.network


# --- YouTube ----------------------------------------------------------------

async def test_youtube_track_extract():
    kind, tr = await audio.extract('https://www.youtube.com/watch?v=dQw4w9WgXcQ')
    assert kind == 'track'
    assert tr.title
    assert tr.url


async def test_youtube_search_query_extract():
    """Текстовый запрос идёт через ytsearch и приходит как плейлист из одного трека"""
    kind, payload = await audio.extract('daft punk get lucky')
    assert kind == 'playlist'
    assert payload.tracks and payload.tracks[0].title


async def test_ytmusic_playlist_extract():
    if ytm is None:
        pytest.skip('ytmusicapi недоступен')
    # выдача плавает: часть результатов приходит без browseId, поэтому пробуем
    # несколько запросов, прежде чем сдаться. Плюс YouTube отдаёт антибот-страницу
    # примерно на 10% запросов, и ytmusicapi разбирает её как JSON — это сбой
    # окружения, а не проверяемого кода, падать по нему тест не должен
    pid = ''
    blocked = 0
    for query in ('lofi hip hop', 'jazz classics', 'rock hits'):
        try:
            found = ytm.search(query, filter='playlists', limit=10) or []
        except json.JSONDecodeError:
            blocked += 1
            continue
        pid = next((b for b in ((f.get('browseId') or '').removeprefix('VL') for f in found)
                    if b and not b.startswith('MPSP')), '')
        if pid:
            break
    # MPSP... это авто-микс, а не плейлист: get_playlist отдаёт ответ без contents,
    # и yt-dlp на нём тоже спотыкается. Такие кандидаты берём мимо
    if not pid:
        pytest.skip('YT Music заблокировал запросы' if blocked
                    else 'YT Music не вернул пригодных плейлистов')
    kind, info = await audio.extract(f'https://music.youtube.com/playlist?list={pid}')
    assert kind == 'playlist'
    assert info.tracks and all(t.title for t in info.tracks)


async def test_youtube_resolve_produces_stream_url():
    kind, tr = await audio.extract('https://www.youtube.com/watch?v=dQw4w9WgXcQ')
    data = await _resolved_data(tr)
    assert data.get('url', '').startswith('http')
    assert data.get('acodec')


# --- Spotify ----------------------------------------------------------------

async def test_spotify_track_extract():
    if sp is None:
        pytest.skip('Spotify-клиент не сконфигурирован')
    kind, tr = await audio.extract('https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT')
    assert kind == 'track'
    assert tr.artist and tr.duration > 0


async def test_spotify_intl_link_extract():
    if sp is None:
        pytest.skip('Spotify-клиент не сконфигурирован')
    kind, tr = await audio.extract('https://open.spotify.com/intl-ru/track/4cOdK2wGLETKBW3PvgPWqT')
    assert kind == 'track' and tr.title


async def test_spotify_album_extract():
    if sp is None:
        pytest.skip('Spotify-клиент не сконфигурирован')
    kind, info = await audio.extract('https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy')
    assert kind == 'playlist' and info.kind == 'album'
    assert len(info.tracks) > 1


async def test_spotify_playlist_extract():
    if sp is None:
        pytest.skip('Spotify-клиент не сконфигурирован')
    own = sp.current_user_playlists(limit=1).get('items') or []
    if not own:
        pytest.skip('у аккаунта нет плейлистов')
    kind, info = await audio.extract(f"https://open.spotify.com/playlist/{own[0]['id']}")
    assert kind == 'playlist'
    assert info.tracks, 'плейлист разобрался в пустой список — проверь ключ элемента'


# --- Yandex Music -----------------------------------------------------------

async def test_yandex_track_extract():
    if yandex_client is None:
        pytest.skip('Yandex-клиент не сконфигурирован')
    kind, tr = await audio.extract('https://music.yandex.ru/album/4058886/track/33429269')
    assert kind == 'track' and tr.title


async def test_yandex_album_extract():
    if yandex_client is None:
        pytest.skip('Yandex-клиент не сконфигурирован')
    kind, info = await audio.extract('https://music.yandex.ru/album/4058886')
    assert kind == 'playlist' and info.tracks


async def test_yandex_resolve_picks_best_codec():
    if yandex_client is None:
        pytest.skip('Yandex-клиент не сконфигурирован')
    kind, tr = await audio.extract('https://music.yandex.ru/album/4058886/track/33429269')
    data = await _resolved_data(tr)
    assert data['url'].startswith('http')
    assert data['acodec'] in ('flac', 'mp3', 'aac', 'he-aac')


# --- SoundCloud -------------------------------------------------------------

async def test_soundcloud_track_extract():
    import asyncio

    loop = asyncio.get_running_loop()
    found = await loop.run_in_executor(
        None, lambda: sc_ytdl.extract_info('scsearch1:lofi', download=False))
    entries = [e for e in ((found or {}).get('entries') or []) if e]
    if not entries:
        pytest.skip('SoundCloud не вернул результатов поиска')
    url = entries[0].get('webpage_url')
    kind, tr = await audio.extract(url)
    assert kind == 'track' and tr.title


# --- вспомогательное --------------------------------------------------------

async def _resolved_data(tr) -> dict:
    """Прогнать resolver и вернуть сырые данные, не поднимая ffmpeg"""
    import audio.source as src

    captured = {}

    class Probe(src.OpusAudioSource):
        def __init__(self, url, *, data, codec=None, **kw):
            captured.update(data)
            captured['url'] = url

        def cleanup(self):
            pass

    original = src.OpusAudioSource.from_resolved.__func__
    try:
        src.OpusAudioSource.from_resolved = classmethod(
            lambda cls, data, *, start=0.0: Probe(data.get('url'), data=data)
        )
        await tr.make_source()
    finally:
        src.OpusAudioSource.from_resolved = classmethod(original)
    return captured
