"""ffmpeg-обёртка для воспроизведения стрим-URL с упреждающей буферизацией кадров."""
import logging
import os
import queue
import threading
import time
from pathlib import Path

import discord


_log = logging.getLogger('audio').info


# Локальный бинарник в third_party/ffmpeg/bin/ или фолбэк на PATH-lookup
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOCAL_FFMPEG = _PROJECT_ROOT / 'third_party' / 'ffmpeg' / 'bin' / ('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')
ffmpeg_path = str(_LOCAL_FFMPEG) if _LOCAL_FFMPEG.exists() else 'ffmpeg'


# Параметры входа: -nostdin исключает странные стопы при перехвате стдина,
# группа -reconnect_* возвращает поток после TCP-разрывов и HTTP-ошибок (требует ffmpeg ≥ 4.4.1),
# -rw_timeout 15s даёт CDN время на медленные ответы, не зависая навсегда,
# -thread_queue_size 4096 убирает «Thread queue blocking» при высоком битрейте Opus-passthrough.
# ВАЖНО: -reconnect_at_eof здесь НЕ ставим — для VOD это заставляет ffmpeg
# переподключаться на легитимном конце трека вместо завершения (зависание на стыке треков).
_BEFORE_OPTIONS = (
    '-nostdin '
    '-reconnect 1 -reconnect_streamed 1 '
    '-reconnect_on_network_error 1 -reconnect_on_http_error 4xx,5xx '
    '-reconnect_delay_max 5 '
    '-rw_timeout 15000000 '
    '-multiple_requests 1 '
    '-thread_queue_size 4096'
)

# Параметры кодирования libopus: -application audio переключает кодек в музыкальный режим
# (вместо voip), -vbr on даёт лучшее качество при том же среднем битрейте,
# -compression_level 10 — максимум усилий кодера. На пути copy игнорируются.
_OUTPUT_OPTIONS = '-vn -application audio -vbr on -compression_level 10'

# Битрейт libopus при перекодировании (AAC/MP4 → Opus для Yandex/Spotify/SoundCloud).
# 192 кбит/с — заметный апгрейд над defaults 128, при этом укладывается в boost-2 сервера.
# Для copy-пути значение игнорируется (поток отдаётся как есть).
_OPUS_BITRATE = 192


# --- Параметры упреждающей буферизации -------------------------------------
# Один аудио-кадр Discord = 20 мс. Размеры буфера считаем в кадрах через эту длину.
_FRAME_SECONDS = 0.02
# Сколько секунд аудио держим в буфере: банк на случай сетевых заминок/троттлинга.
_BUFFER_SECONDS = 30.0
# Сколько накопить перед самым первым read() — стартовая подушка.
_PREFILL_SECONDS = 0.8
# Потолок ожидания префилла, чтобы не зависнуть навсегда на «медленном» потоке.
_PREFILL_TIMEOUT = 8.0

_BUFFER_CAP = max(1, int(_BUFFER_SECONDS / _FRAME_SECONDS))
_PREFILL_FRAMES = max(1, int(_PREFILL_SECONDS / _FRAME_SECONDS))

_EOF = object()  # маркер конца потока внутри буфера


ffmpeg_options = {
    'before_options': _BEFORE_OPTIONS,
    'options': _OUTPUT_OPTIONS,
}


class OpusAudioSource(discord.FFmpegOpusAudio):
    """FFmpegOpusAudio с метаданными трека и упреждающей буферизацией кадров.

    Проблема, которую решает буфер: discord.py читает кадры строго по 20 мс прямо
    из пайпа ffmpeg, а тактирование плеера абсолютное. Любая сетевая заминка
    (троттлинг googlevideo, реконнект CDN) задерживает read(); плеер отстаёт от
    стенных часов и потом досылает накопленные кадры пачкой без сна — звук
    «ускоряется» (чипманк). Недоборы дают заикания. Чтобы разорвать связь между
    сетью и тактом, фоновый поток жадно вычитывает кадры из ffmpeg в буфер
    (~30 с), а read() отдаёт их Discord ровно по тику. Пока буфер не опустел,
    сетевые заминки до плеера не доходят.
    """

    def __init__(self, source, *, data, codec=None, executable=None, **kwargs):
        super().__init__(source, codec=codec, executable=executable, **kwargs)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')
        self._buffer: 'queue.Queue' = queue.Queue(maxsize=_BUFFER_CAP)
        self._buffer_stop = threading.Event()
        self._prefilled = False
        self._underruns = 0  # сколько раз плеер ждал пустой буфер (диагностика троттлинга)
        self._reader = threading.Thread(
            target=self._fill_buffer, name='opus-prebuffer', daemon=True,
        )
        self._reader.start()

    def _fill_buffer(self) -> None:
        """Фоновый продюсер: тянет кадры из ffmpeg в буфер, пока не EOF/stop."""
        try:
            while not self._buffer_stop.is_set():
                try:
                    data = super().read()
                except Exception:
                    break
                if not data:
                    break
                # put с таймаутом — чтобы реагировать на stop, даже если буфер полон
                while not self._buffer_stop.is_set():
                    try:
                        self._buffer.put(data, timeout=0.5)
                        break
                    except queue.Full:
                        continue
        finally:
            try:
                self._buffer.put(_EOF, timeout=1.0)
            except queue.Full:
                pass  # буфер забит и его уже не читают — потребитель не придёт

    def _wait_prefill(self) -> None:
        deadline = time.monotonic() + _PREFILL_TIMEOUT
        while (self._buffer.qsize() < _PREFILL_FRAMES
               and self._reader.is_alive()
               and not self._buffer_stop.is_set()
               and time.monotonic() < deadline):
            time.sleep(0.02)

    def read(self) -> bytes:
        if not self._prefilled:
            self._prefilled = True
            self._wait_prefill()
        waited = False
        while not self._buffer_stop.is_set():
            try:
                item = self._buffer.get(timeout=0.5)
            except queue.Empty:
                waited = True  # буфер опустел: сеть не успевает за реалтаймом
                continue
            if item is _EOF:
                return b''
            if waited:
                self._underruns += 1
            return item
        return b''

    def cleanup(self) -> None:
        if getattr(self, '_underruns', 0):
            _log(f'prebuffer underruns={self._underruns} (сеть не успевала) for {self.title!r}')
        self._buffer_stop.set()
        # освобождаем слот, если продюсер завис на put в полный буфер
        try:
            self._buffer.get_nowait()
        except Exception:
            pass
        super().cleanup()  # убивает ffmpeg -> super().read() в продюсере вернёт b'' -> поток выйдет
        reader = getattr(self, '_reader', None)
        if reader is not None and reader.is_alive() and reader is not threading.current_thread():
            reader.join(timeout=1.0)

    @classmethod
    def from_resolved(cls, data: dict, *, start: float = 0.0) -> 'OpusAudioSource':
        """Собрать источник из готовых yt-dlp-подобных данных (поле url — стрим).

        start>0 добавляет ffmpeg-флаг -ss перед -i (быстрый поиск, для перемотки).
        """
        stream_url = data.get('url')
        if not stream_url:
            raise RuntimeError('Не удалось получить стрим-URL.')
        acodec = (data.get('acodec') or '').lower()
        can_copy = acodec.startswith('opus')
        _log(f'build source (acodec={acodec} abr={data.get("abr")} copy={can_copy} start={start:.1f})')
        before = _BEFORE_OPTIONS
        if start > 0:
            before = f'-ss {start:.3f} {before}'
        return cls(
            stream_url,
            data=data,
            codec='copy' if can_copy else None,
            bitrate=_OPUS_BITRATE,
            executable=ffmpeg_path,
            before_options=before,
            options=_OUTPUT_OPTIONS,
        )
