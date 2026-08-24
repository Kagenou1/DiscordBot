"""OpusAudioSource: сборка ffmpeg-команды и упреждающая буферизация"""
import queue
import threading
import time

import pytest

from audio import source as src


def test_from_resolved_requires_stream_url():
    with pytest.raises(RuntimeError):
        src.OpusAudioSource.from_resolved({})


def test_before_options_exclude_reconnect_at_eof():
    """С -reconnect_at_eof ffmpeg переподключается на легитимном конце трека"""
    assert '-reconnect_at_eof' not in src._BEFORE_OPTIONS
    assert '-nostdin' in src._BEFORE_OPTIONS
    assert '-reconnect 1' in src._BEFORE_OPTIONS


def test_buffer_sizes_derive_from_frame_length():
    assert src._BUFFER_CAP == int(src._BUFFER_SECONDS / src._FRAME_SECONDS)
    assert src._PREFILL_FRAMES == int(src._PREFILL_SECONDS / src._FRAME_SECONDS)
    assert src._PREFILL_FRAMES < src._BUFFER_CAP


@pytest.mark.parametrize('acodec,expect_copy', [
    ('opus', True), ('opus (Opus)', True), ('mp4a.40.2', False), ('', False), (None, False),
])
def test_copy_path_chosen_for_opus(monkeypatch, acodec, expect_copy):
    captured = {}

    class Probe(src.OpusAudioSource):
        def __init__(self, stream_url, *, data, codec=None, **kw):
            captured.update(url=stream_url, codec=codec, before=kw.get('before_options'))

        def cleanup(self):
            pass

    monkeypatch.setattr(src, 'OpusAudioSource', Probe)
    Probe.from_resolved({'url': 'https://s/x', 'acodec': acodec})
    assert (captured['codec'] == 'copy') is expect_copy


def test_seek_prepends_ss_flag(monkeypatch):
    captured = {}

    class Probe(src.OpusAudioSource):
        def __init__(self, stream_url, *, data, codec=None, **kw):
            captured.update(before=kw.get('before_options'))

        def cleanup(self):
            pass

    monkeypatch.setattr(src, 'OpusAudioSource', Probe)
    Probe.from_resolved({'url': 'https://s/x', 'acodec': 'opus'}, start=95.5)
    assert captured['before'].startswith('-ss 95.500 ')


def test_no_ss_flag_without_seek(monkeypatch):
    captured = {}

    class Probe(src.OpusAudioSource):
        def __init__(self, stream_url, *, data, codec=None, **kw):
            captured.update(before=kw.get('before_options'))

        def cleanup(self):
            pass

    monkeypatch.setattr(src, 'OpusAudioSource', Probe)
    Probe.from_resolved({'url': 'https://s/x'})
    assert '-ss' not in captured['before']


# --- поведение буфера без запуска ffmpeg ------------------------------------

class BufferOnly(src.OpusAudioSource):
    """Даёт прогнать логику буфера, не поднимая ffmpeg"""

    def __init__(self, frames, delay=0.0):
        self._frames = list(frames)
        self._delay = delay
        self.title = 'test'
        self.data = {}
        self._buffer = queue.Queue(maxsize=src._BUFFER_CAP)
        self._buffer_stop = threading.Event()
        self._prefilled = True  # префилл не нужен: проверяем именно выдачу кадров
        self._eof_seen = False
        self._frames_sent = 0
        self._frames_buffered = 0  # wait_ready считает по нему, дубль обязан вести
        self._underruns = 0
        self._reader = threading.Thread(target=self._fill_buffer, daemon=True)
        self._reader.start()

    def _raw_read(self):
        if self._delay:
            time.sleep(self._delay)
        return self._frames.pop(0) if self._frames else b''

    def _fill_buffer(self):
        try:
            while not self._buffer_stop.is_set():
                data = self._raw_read()
                if not data:
                    break
                while not self._buffer_stop.is_set():
                    try:
                        self._buffer.put(data, timeout=0.5)
                        self._frames_buffered += 1
                        break
                    except queue.Full:
                        continue
        finally:
            try:
                self._buffer.put(src._EOF, timeout=1.0)
            except queue.Full:
                pass

    def cleanup(self):
        self._buffer_stop.set()


def test_buffer_delivers_frames_in_order():
    s = BufferOnly([b'a', b'b', b'c'])
    assert [s.read() for _ in range(3)] == [b'a', b'b', b'c']
    assert s.read() == b''  # EOF


def test_buffer_returns_empty_after_stop():
    s = BufferOnly([b'a'] * 10)
    s._buffer_stop.set()
    assert s.read() == b''


def test_underrun_counted_when_producer_lags():
    s = BufferOnly([b'a', b'b'], delay=0.6)
    s.read()
    s.read()
    assert s._underruns >= 1


# --- сбой ffmpeg должен быть отличим от конца трека -------------------------

class _Fake(src.OpusAudioSource):
    """Только поля, которые читает failure_reason"""

    def __init__(self, error=None, frames=0):
        self._current_error = error
        self._frames_sent = frames

    def cleanup(self):
        pass  # базовый cleanup полез бы в неинициализированные поля


def test_failure_reason_reports_ffmpeg_error():
    reason = _Fake(error=RuntimeError('FFmpeg exited with code 1')).failure_reason()
    assert reason == 'FFmpeg exited with code 1'


def test_failure_reason_silent_on_normal_end():
    assert _Fake(error=None, frames=1500).failure_reason() is None


def test_failure_reason_does_not_flag_early_skip():
    """При /skip в первые секунды кадров ноль, но это не сбой"""
    assert _Fake(error=None, frames=0).failure_reason() is None


def test_note_process_failure_survives_missing_process():
    f = _Fake()
    f._note_process_failure()  # не должно бросить, если процесса нет


def test_read_after_eof_returns_empty_immediately():
    """Повторный read() после EOF крутился в цикле вечно и вешал прогон"""
    s = BufferOnly([b'a'])
    assert s.read() == b'a'
    assert s.read() == b''
    for _ in range(3):
        assert s.read() == b''


def test_read_after_eof_does_not_block(monkeypatch):
    """Страховка от возврата зависания: третий read укладывается в такт плеера"""
    s = BufferOnly([b'a'])
    s.read()
    s.read()
    t0 = time.perf_counter()
    s.read()
    assert (time.perf_counter() - t0) < 0.1


# --- интеграция с реальным ffmpeg, без сети ---------------------------------

def _local_source(path, title='тест'):
    """Источник на локальный файл: _BEFORE_OPTIONS заточен под http и сюда не годится"""
    return src.OpusAudioSource(
        str(path), data={'url': str(path), 'title': title},
        executable=src.ffmpeg_path, before_options='-nostdin', options=src._OUTPUT_OPTIONS,
    )


def _need_ffmpeg():
    import pathlib as _p
    if src.ffmpeg_path != 'ffmpeg' and not _p.Path(src.ffmpeg_path).exists():
        pytest.skip('ffmpeg недоступен')


def _drain(source, limit=200):
    frames = 0
    while frames < limit and source.read():
        frames += 1
    return frames


@pytest.mark.integration
def test_broken_input_sets_failure_reason(tmp_path):
    """Упавший ffmpeg должен быть отличим от нормального конца трека

    Регрессия: _check_process_returncode живёт внутри read() базового класса,
    который предбуфер обходит, поэтому 403 приходил в after как error=None
    и очередь молча проматывалась до конца
    """
    _need_ffmpeg()
    source = _local_source(tmp_path / 'нет-такого-файла.opus', 'битый')
    try:
        assert _drain(source, 50) == 0, 'битый вход не должен отдавать кадры'
        reason = source.failure_reason()
        assert reason is not None, 'сбой ffmpeg не дошёл до failure_reason'
        assert 'FFmpeg' in reason or 'code' in reason
    finally:
        source.cleanup()


@pytest.mark.integration
def test_healthy_source_has_no_failure_reason(tmp_path):
    """Обратная проверка: у нормально доигравшего источника причины сбоя нет"""
    import subprocess
    _need_ffmpeg()
    wav = tmp_path / 'tone.wav'
    subprocess.run([src.ffmpeg_path, '-hide_banner', '-loglevel', 'error', '-y',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1', str(wav)],
                   check=True, capture_output=True)

    source = _local_source(wav, 'тон')
    try:
        frames = _drain(source)
        assert frames > 10, f'ожидали поток кадров, получили {frames}'
        assert source.failure_reason() is None
        assert source._frames_sent == frames
    finally:
        source.cleanup()


# --- готовность источника до старта плеера -----------------------------------

def test_wait_ready_ждёт_стартовый_запас():
    """Плеер стартует по абсолютным часам, поэтому звук должен быть заранее"""
    src_obj = BufferOnly([b'x'] * (src._PREFILL_FRAMES + 5), delay=0.001)
    try:
        assert src_obj.wait_ready(timeout=5) is True
        assert src_obj._buffer.qsize() >= src._PREFILL_FRAMES
    finally:
        src_obj.cleanup()


def test_wait_ready_не_ждёт_когда_буфер_уже_полон():
    """Заготовленный заранее трек не должен платить ожиданием"""
    src_obj = BufferOnly([b'x'] * (src._PREFILL_FRAMES + 5))
    try:
        src_obj.wait_ready(timeout=5)
        t0 = time.perf_counter()
        assert src_obj.wait_ready(timeout=5) is True
        assert (time.perf_counter() - t0) < 0.1
    finally:
        src_obj.cleanup()


def test_wait_ready_отвечает_нет_если_звука_не_было():
    """Поток, который не открылся: ffmpeg кончился, кадров нет"""
    src_obj = BufferOnly([])
    try:
        assert src_obj.wait_ready(timeout=2) is False
    finally:
        src_obj.cleanup()


def test_wait_ready_принимает_трек_короче_запаса():
    """Короткий трек не наберёт стартовый запас, но играть его надо"""
    src_obj = BufferOnly([b'x'] * 2)
    try:
        assert src_obj.wait_ready(timeout=2) is True
    finally:
        src_obj.cleanup()


def test_wait_ready_не_виснет_на_молчащем_потоке():
    """Ограничение по времени: иначе ког ждал бы вечно"""
    src_obj = BufferOnly([b'x'] * (src._PREFILL_FRAMES + 5), delay=0.5)
    try:
        t0 = time.perf_counter()
        src_obj.wait_ready(timeout=0.3)
        assert (time.perf_counter() - t0) < 1.5
    finally:
        src_obj.cleanup()


def test_wait_ready_прерывается_остановкой():
    src_obj = BufferOnly([b'x'] * (src._PREFILL_FRAMES + 5), delay=0.5)
    try:
        src_obj._buffer_stop.set()
        assert src_obj.wait_ready(timeout=2) is False
    finally:
        src_obj.cleanup()



@pytest.mark.parametrize('data,expected', [
    ({'hls': True}, True),                       # VK ставит флаг сам
    ({'protocol': 'm3u8_native'}, True),         # SoundCloud: hls_aac_160k
    ({'url': 'https://cdn/index.m3u8'}, True),
    ({'protocol': 'https', 'url': 'https://rr3.googlevideo.com/videoplayback?x=1'}, False),
    ({'url': 'https://storage.yandex.net/get-mp3/x'}, False),
    ({}, False),
])
def test_is_hls_looks_at_the_stream_not_the_provider(data, expected):
    """HLS отдаёт не только VK: SoundCloud тоже, и завтра может кто угодно"""
    assert src._is_hls(data) is expected


@pytest.mark.parametrize('data,seconds', [
    # HLS: трек влезает целиком, иначе ffmpeg замирает посреди сегмента
    ({'hls': True, 'duration': 248}, 278),
    ({'protocol': 'm3u8_native', 'duration': 300}, 330),
    ({'hls': True, 'duration': 10_000}, src._BUFFER_MAX_SECONDS),
    ({'hls': True, 'duration': 0}, src._BUFFER_SECONDS),
    ({'hls': True, 'duration': 'мусор'}, src._BUFFER_SECONDS),
    # обычный HTTP переживает простой: ffmpeg переподключается по смещению
    ({'protocol': 'https', 'duration': 234}, src._BUFFER_SECONDS),
    ({'duration': 226}, src._BUFFER_SECONDS),
])
def test_buffer_cap_grows_only_for_hls(data, seconds):
    """Замер простоя 300 с: обычный HTTP 233.9 с из 234, HLS 241.5 с из 248"""
    assert src._buffer_cap(data) == int(seconds / src._FRAME_SECONDS)


def test_buffer_cap_reaches_the_queue(monkeypatch):
    """Потолок обязан дойти до самой очереди, а не остаться в функции"""
    from discord.utils import MISSING

    monkeypatch.setattr(src.OpusAudioSource, '_fill_buffer', lambda self: None)
    monkeypatch.setattr(src.discord.FFmpegOpusAudio, '__init__',
                        lambda self, *a, **kw: None)
    hls = src.OpusAudioSource('u', data={'url': 'u', 'hls': True, 'duration': 248})
    hls._process = MISSING  # discord.py читает его при сборке мусора
    plain = src.OpusAudioSource('u', data={'url': 'u', 'duration': 248})
    plain._process = MISSING

    assert hls._buffer.maxsize == src._buffer_cap({'hls': True, 'duration': 248})
    assert hls._buffer.maxsize > src._BUFFER_CAP, 'HLS должен влезать целиком'
    assert plain._buffer.maxsize == src._BUFFER_CAP, 'обычному HTTP хватает запаса'
