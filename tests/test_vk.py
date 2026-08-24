"""Провайдер VK Музыки: маршрутизация ссылок, разбор записей, маршрут потока"""
import socket
import threading

import pytest

import audio
from audio import source as src
from audio.vk import client as vk_client
from audio.vk import parse as vk_parse
from audio.vk import proxy as vk_proxy


# --- маршрутизация ------------------------------------------------------------

@pytest.mark.parametrize('url', [
    'https://vk.ru/audio-26549346_456239443_59159cef5d080f5450',
    'https://vk.com/audio-2001746599_34746599',
    'https://m.vk.com/audio-2001844083_29844083',
    'https://vk.com/music/playlist/-25611523_85178143',
    'https://vk.ru/music/album/-2000984503_984503',
    'https://vk.ru/artist/linkinpark/releases?z=audio_playlist-2000984503_984503',
    # голая форма без music/: именно её отдаёт кнопка «поделиться»
    'https://vk.ru/audio_playlist157818519_24',
    'https://vk.ru/audio_playlist-2000984503_984503%2Fc468f3a862b6f73b55',
])
def test_vk_music_links_route_to_provider(url):
    assert audio._VK_RE.search(url), 'ссылка не распознана как VK Музыка'


@pytest.mark.parametrize('url', [
    'https://vk.com/durov',
    'https://vk.ru/feed',
    'https://vk.com/video-1_2',
    'https://vkvideo.ru/video-1_2',
])
def test_other_vk_links_not_intercepted(url):
    """Профили и видео VK музыкальному провайдеру не принадлежат"""
    assert not audio._VK_RE.search(url)


@pytest.mark.parametrize('url', [
    'https://vk.ru/audio_playlist157818519_24',
    'https://vk.ru/audio_playlist-2000984503_984503%2Fc468f3a862b6f73b55',
    'https://vk.ru/music/playlist/-25611523_85178143',
    'https://vk.ru/artist/linkinpark/releases?z=audio_playlist-2000984503_984503',
])
def test_playlist_urls_go_through_flat_client(url):
    """Регрессия: форму audio_playlist без music/ не знал ни один экстрактор,
    ссылка уходила по треку и падала на первом же недоступном"""
    from audio.vk.extractor import VKMusicPlaylistIE, VKMusicTrackIE
    assert vk_parse.VK_PLAYLIST_RE.search(url), 'должен идти плоским клиентом'
    assert VKMusicPlaylistIE.suitable(url)
    assert not VKMusicTrackIE.suitable(url)


def test_provider_regexes_do_not_overlap():
    url = 'https://vk.ru/audio-1_2'
    assert not audio._SPOTIFY_RE.search(url)
    assert not audio._YANDEX_RE.search(url)
    assert not audio._SOUNDCLOUD_RE.search(url)


# --- разбор записей -----------------------------------------------------------

def test_flat_playlist_entry_becomes_track():
    """У плоских записей нет webpage_url, ссылка лежит в url"""
    track = vk_parse.entry_to_track({
        'url': 'https://vk.ru/audio-1_2_abc',
        'track': 'Название',
        'artist': 'Исполнитель',
        'duration': 210,
    }, resolver=None)
    assert track.url == 'https://vk.ru/audio-1_2_abc'
    assert track.title == 'Название'
    assert track.artist == 'Исполнитель'
    assert track.duration == 210.0


def test_title_taken_from_track_field():
    """В title у VK склеены исполнитель и название, отдельное поле чище"""
    track = vk_parse.entry_to_track({
        'url': 'u', 'title': 'Исполнитель - Название', 'track': 'Название',
    }, resolver=None)
    assert track.title == 'Название'


def test_entry_without_url_dropped():
    assert vk_parse.entry_to_track({'title': 'Без ссылки'}, resolver=None) is None
    assert vk_parse.entry_to_track(None, resolver=None) is None


# --- маршрут потока -----------------------------------------------------------

def _before_options(monkeypatch, data, **kwargs):
    """Собрать источник, перехватив before_options до запуска ffmpeg

    Дубль ставит поля, которые читает cleanup при сборке мусора: без них
    падение всплывает предупреждением и прячет настоящие ошибки
    """
    import threading

    from discord.utils import MISSING

    seen = {}

    def fake_init(self, url, *, data=None, codec=None, bitrate=None,
                  executable=None, before_options=None, options=None):
        seen['before'] = before_options
        # discord.py в __del__ идёт в _kill_process и читает _process
        self._process = MISSING
        self._buffer_stop = threading.Event()
        self._frames_sent = 0
        self._frames_buffered = 0
        self._eof_seen = False

    monkeypatch.setattr(src.OpusAudioSource, '__init__', fake_init)
    src.OpusAudioSource.from_resolved(data, **kwargs)
    return seen['before']


def test_proxy_reaches_ffmpeg_command(monkeypatch):
    before = _before_options(monkeypatch, {
        'url': 'https://cdn/index.m3u8', 'http_proxy': 'http://127.0.0.1:8890'})
    assert '-http_proxy http://127.0.0.1:8890' in before


def test_no_proxy_means_no_flag(monkeypatch):
    before = _before_options(monkeypatch, {'url': 'https://cdn/index.m3u8'})
    assert '-http_proxy' not in before


def test_seek_keeps_proxy(monkeypatch):
    """Регрессия: перемотка пересобирает источник из тех же данных

    Маршрут поэтому лежит в них, а не в параметре: у VK ссылка привязана к
    адресу запросившего, и поток после перемотки ушёл бы мимо прокси и в 403
    """
    data = {'url': 'https://cdn/index.m3u8', 'http_proxy': 'http://127.0.0.1:8890'}
    before = _before_options(monkeypatch, data, start=42.0)
    assert '-http_proxy http://127.0.0.1:8890' in before
    assert '-ss 42.000' in before


# --- настройки ----------------------------------------------------------------

def test_provider_disabled_without_cookies(monkeypatch):
    """Куки обязательны: без них VK молча отдаёт 31 секунду вместо трека"""
    monkeypatch.setattr(vk_client, 'vk_cookies', '')
    assert vk_client.configured() is False


def test_missing_cookie_file_disables_provider(monkeypatch, tmp_path):
    monkeypatch.setattr(vk_client, 'vk_cookies', str(tmp_path / 'нет.txt'))
    assert vk_client.configured() is False


def test_proxy_port_parsed_from_url():
    assert vk_proxy.port_of('http://127.0.0.1:8890') == 8890
    assert vk_proxy.port_of('http://127.0.0.1') == 0
    assert vk_proxy.port_of('мусор') == 0


def test_flag_strips_multiple_requests(monkeypatch):
    """Замер: связка -multiple_requests с переподключениями стоит 10933 мс
    до первого звука против 1159 без неё. По отдельности обе опции безвредны.

    Набор подменяем, чтобы проверялся сам механизм, а не текущее содержимое
    константы: опция снята на боевую проверку и может вернуться
    """
    monkeypatch.setattr(src, '_BEFORE_OPTIONS', '-nostdin -multiple_requests 1 -reconnect 1')
    before = _before_options(monkeypatch, {
        'url': 'https://cdn/index.m3u8', 'hls': True})
    assert '-multiple_requests' not in before
    assert '-http_persistent 0' in before, 'HLS не должен переиспользовать соединение'
    assert '-reconnect 1' in before, 'остальной набор трогать не нужно'


def test_hls_reloads_failed_segment(monkeypatch):
    """По умолчанию seg_max_retry=0 и сегмент с ошибкой ffmpeg пропускает

    Соединение заготовки простаивает, пока играет предыдущий трек, и CDN его
    закрывает. Замер на паузе 150 с: без опции 245.9 с при заявленных 248 и
    битые пакеты, с ней 248.5 с и ни одной ошибки
    """
    before = _before_options(monkeypatch, {
        'url': 'https://cdn/index.m3u8', 'hls': True})
    assert f'-seg_max_retry {src._SEG_MAX_RETRY}' in before


def test_hls_options_stay_off_plain_input(monkeypatch):
    """На не-HLS входе ffmpeg падает с «Option seg_max_retry not found»"""
    before = _before_options(monkeypatch, {'url': 'https://cdn/a.m4a'})
    assert '-seg_max_retry' not in before
    assert '-http_persistent' not in before


def test_without_flag_options_unchanged(monkeypatch):
    monkeypatch.setattr(src, '_BEFORE_OPTIONS', '-nostdin -multiple_requests 1 -reconnect 1')
    before = _before_options(monkeypatch, {'url': 'https://cdn/a.m4a'})
    assert '-multiple_requests 1' in before
    assert '-http_persistent' not in before


# --- обложки ------------------------------------------------------------------

def _covers(raw):
    from audio.vk.extractor import VKMusicBaseIE
    return [c['url'] for c in VKMusicBaseIE._covers(raw)]


def test_covers_split_by_comma():
    """Регрессия: VK кладёт размеры в одно поле через запятую

    Целиком строка уходила одним URL, и Discord такую обложку не загружал
    """
    raw = ('https://cdn/a.jpg?size=300x300&type=audio,'
           'https://cdn/a.jpg?size=160x160&type=audio')
    got = _covers(raw)
    assert len(got) == 2
    assert all(',' not in u for u in got)


def test_covers_largest_last():
    """Потребители берут последний элемент как лучший, у VK порядок обратный"""
    raw = 'https://cdn/a.jpg?size=300x300,https://cdn/a.jpg?size=160x160'
    assert _covers(raw)[-1].endswith('size=300x300')


def test_covers_single_url_kept():
    assert _covers('https://cdn/a.jpg') == ['https://cdn/a.jpg']


@pytest.mark.parametrize('raw', ['', None, '   ', 'мусор', ','])
def test_covers_empty_input(raw):
    assert _covers(raw) == []


def test_pipe_waits_without_idle_timeout(monkeypatch):
    """Молчание не повод рвать туннель

    Тайм-аут простоя в 60 с закрывал живое соединение: заготовка следующего
    трека набирает буфер за секунду и замирает посреди скачивания сегмента,
    а ждёт своей очереди минуты. ffmpeg потом видел «Stream ends prematurely»
    """
    import select as select_mod
    from tools import vk_proxy

    seen = []
    real_select = select_mod.select

    def probe(r, w, x, *timeout):
        seen.append(timeout)
        return real_select(r, w, x, *timeout)

    monkeypatch.setattr(vk_proxy.select, 'select', probe)
    left, right = socket.socketpair()
    peer_a, peer_b = socket.socketpair()
    worker = threading.Thread(target=vk_proxy._pipe, args=(right, peer_a), daemon=True)
    worker.start()
    left.sendall(b'ping')
    assert peer_b.recv(4) == b'ping'
    left.close()
    peer_b.close()
    worker.join(timeout=5)
    assert not worker.is_alive(), 'поток не вышел по закрытию обеих сторон'
    assert seen and all(t == () or t[0] is None for t in seen), \
        f'select вызван с тайм-аутом простоя: {seen}'


def test_pipe_half_close_keeps_reverse_direction():
    """EOF одной стороны не должен обрывать встречный поток

    Закрытие обоих сокетов шлёт RST вместо FIN, непрочитанные данные пропадают,
    и в звуке появляется дыра
    """
    from tools import vk_proxy

    left, right = socket.socketpair()
    peer_a, peer_b = socket.socketpair()
    worker = threading.Thread(target=vk_proxy._pipe, args=(right, peer_a), daemon=True)
    worker.start()
    left.shutdown(socket.SHUT_WR)  # клиент дочитал и закрыл отправку
    peer_b.sendall(b'tail')        # сервер досылает хвост
    assert left.recv(4) == b'tail', 'встречное направление оборвано'
    peer_b.close()
    left.close()
    worker.join(timeout=5)
    assert not worker.is_alive()


def test_pipe_reads_ahead_while_client_stalls():
    """Прокси обязан дочитывать ответ, даже когда ffmpeg перестал читать

    Прямая пересылка связывала скорость CDN со скоростью чтения: заготовка
    набирала буфер и замирала, соединение простаивало посреди ответа, и CDN
    ВКонтакте его закрывал — 241.5 с звука из 248 на паузе 300 с.

    Замер этого же дубля: с опережением 8 МБ уходят за 0.0 с, без него
    отправка встаёт на 0.81 МБ — столько вмещают буферы сокетов
    """
    from tools import vk_proxy

    left, right = socket.socketpair()
    peer_a, peer_b = socket.socketpair()
    worker = threading.Thread(target=vk_proxy._pipe, args=(right, peer_a), daemon=True)
    worker.start()
    try:
        # 8 МБ: заведомо больше буферов сокетов (0.8 МБ), но меньше потолка
        payload = b'x' * (8 * 1024 * 1024)
        peer_b.settimeout(5)
        sent = 0
        while sent < len(payload):
            sent += peer_b.send(payload[sent:sent + 65536])
        assert sent == len(payload), 'ответ не выкачан, соединение осталось бы висеть'

        left.settimeout(10)
        got = 0
        while got < len(payload):
            got += len(left.recv(1 << 16))
        assert got == len(payload), 'опережение потеряло данные'
    finally:
        left.close()
        peer_b.close()
    worker.join(timeout=5)
    assert not worker.is_alive()
