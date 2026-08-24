"""ffmpeg-обёртка для воспроизведения стрим-URL с упреждающей буферизацией кадров"""
import logging
import os
import queue
import threading
import time
from pathlib import Path

import discord


_log = logging.getLogger('audio').info


# локальный бинарник в third_party/ffmpeg/bin/ или фолбэк на PATH-lookup
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOCAL_FFMPEG = _PROJECT_ROOT / 'third_party' / 'ffmpeg' / 'bin' / ('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')
ffmpeg_path = str(_LOCAL_FFMPEG) if _LOCAL_FFMPEG.exists() else 'ffmpeg'


# -nostdin исключает стопы при перехвате стдина
# -reconnect_* возвращает поток после TCP-разрывов и HTTP-ошибок, требует ffmpeg >= 4.4.1
# -rw_timeout 15s даёт CDN время на медленный ответ, не зависая навсегда
# -thread_queue_size 4096 убирает «Thread queue blocking» при высоком битрейте Opus-passthrough
# -reconnect_at_eof НЕ ставим: для VOD это заставляет переподключаться на легитимном
# конце трека вместо завершения, воспроизведение виснет на стыке треков
_BEFORE_OPTIONS = (
    '-nostdin '
    '-reconnect 1 -reconnect_streamed 1 '
    '-reconnect_on_network_error 1 -reconnect_on_http_error 4xx,5xx '
    '-reconnect_delay_max 5 '
    '-rw_timeout 15000000 '
    # -multiple_requests снят на боевую проверку: обоснования у него не было,
    # а на HLS ВКонтакте в связке с переподключениями он давал 10933 мс до
    # первого звука против 1159 без него. Влияние на остальных провайдеров
    # не мерено — вернуть одной строкой, если станет хуже
    # '-multiple_requests 1 '
    '-thread_queue_size 4096'
)

# -application audio переключает libopus в музыкальный режим вместо voip,
# -vbr on даёт лучшее качество при том же среднем битрейте,
# -compression_level 10 — максимум усилий кодера; на пути copy игнорируются
_OUTPUT_OPTIONS = '-vn -application audio -vbr on -compression_level 10'

# битрейт libopus при перекодировании AAC/MP4 -> Opus для Yandex/Spotify/SoundCloud;
# 192 кбит/с укладывается в boost-2 сервера, на copy-пути значение игнорируется
_OPUS_BITRATE = 192

# сколько раз перезагружать сегмент HLS, прежде чем сдаться
_SEG_MAX_RETRY = 5


# --- параметры упреждающей буферизации -------------------------------------
# один аудиокадр Discord = 20 мс, размеры буфера считаем в кадрах через эту длину
_FRAME_SECONDS = 0.02
# сколько секунд аудио держим в буфере
_BUFFER_SECONDS = 30.0
# сколько накопить перед первым read()
_PREFILL_SECONDS = 0.8
# потолок ожидания префилла, чтобы не зависнуть на медленном потоке
_PREFILL_TIMEOUT = 8.0

# Потолок для HLS-входа. Заготовка поднимается заранее, набирает буфер за
# секунду и замирает посреди сегмента: соединение простаивает, пока играет
# предыдущий трек. Обычный HTTP это переживает — ffmpeg переподключается по
# смещению в байтах и не теряет ничего (замер: 233.9 с из 234 при простое
# 300 с). У HLS смещения нет, переподключение начинает сегмент заново либо
# пропускает его: на CDN ВКонтакте тот же простой дал 241.5 с из 248 и битые
# пакеты. Влезающий целиком трек ffmpeg дочитывает и закрывает соединение сам
_BUFFER_MAX_SECONDS = 1800.0

_BUFFER_CAP = max(1, int(_BUFFER_SECONDS / _FRAME_SECONDS))
_BUFFER_MAX_CAP = max(1, int(_BUFFER_MAX_SECONDS / _FRAME_SECONDS))
_PREFILL_FRAMES = max(1, int(_PREFILL_SECONDS / _FRAME_SECONDS))


def _is_hls(data: dict) -> bool:
    """HLS ли вход: у него свой демуксер и своё поведение при обрыве

    Проверяем не провайдера, а сам поток: HLS отдают и VK, и SoundCloud
    (protocol=m3u8_native, format_id=hls_aac_160k), и завтра может отдать
    любой другой
    """
    return bool(data.get('hls')
                or 'm3u8' in (data.get('protocol') or '')
                or '.m3u8' in (data.get('url') or ''))


def _buffer_cap(data: dict) -> int:
    """Сколько кадров держать: весь трек для HLS, иначе обычный запас"""
    if not _is_hls(data):
        return _BUFFER_CAP
    try:
        seconds = float(data.get('duration') or 0.0)
    except (TypeError, ValueError):
        return _BUFFER_CAP
    if seconds <= 0:
        return _BUFFER_CAP
    # запас на расхождение заявленной длительности с фактической
    frames = int((seconds + 30.0) / _FRAME_SECONDS)
    return max(_BUFFER_CAP, min(frames, _BUFFER_MAX_CAP))


_EOF = object()  # маркер конца потока внутри буфера


ffmpeg_options = {
    'before_options': _BEFORE_OPTIONS,
    'options': _OUTPUT_OPTIONS,
}


class OpusAudioSource(discord.FFmpegOpusAudio):
    """FFmpegOpusAudio с метаданными трека и упреждающей буферизацией кадров

    discord.py читает кадры по 20 мс прямо из пайпа ffmpeg, а тактирование плеера
    абсолютное. Сетевая заминка задерживает read(), плеер отстаёт от стенных часов
    и затем досылает накопленные кадры пачкой без сна — звук ускоряется. Недоборы
    дают заикания. Фоновый поток вычитывает кадры из ffmpeg в буфер (~30 с),
    read() отдаёт их ровно по тику; пока буфер не пуст, сеть до плеера не доходит
    """

    def __init__(self, source, *, data, codec=None, executable=None, **kwargs):
        super().__init__(source, codec=codec, executable=executable, **kwargs)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')
        self._buffer: 'queue.Queue' = queue.Queue(maxsize=_buffer_cap(data))
        self._buffer_stop = threading.Event()
        self._prefilled = False
        self._eof_seen = False
        self._frames_sent = 0  # сколько кадров реально ушло плееру
        self._frames_buffered = 0  # сколько кадров легло в буфер, без маркера конца
        self._underruns = 0  # сколько раз плеер ждал пустой буфер
        self._reader = threading.Thread(
            target=self._fill_buffer, name='opus-prebuffer', daemon=True,
        )
        self._reader.start()

    def _fill_buffer(self) -> None:
        """Продюсер: тянет кадры из ffmpeg в буфер до EOF или stop"""
        try:
            while not self._buffer_stop.is_set():
                try:
                    data = super().read()
                except Exception:
                    break
                if not data:
                    break
                # put с таймаутом, чтобы реагировать на stop при полном буфере
                while not self._buffer_stop.is_set():
                    try:
                        self._buffer.put(data, timeout=0.5)
                        self._frames_buffered += 1
                        break
                    except queue.Full:
                        continue
        finally:
            self._note_process_failure()
            try:
                self._buffer.put(_EOF, timeout=1.0)
            except queue.Full:
                pass  # буфер забит и его уже не читают

    def _note_process_failure(self) -> None:
        """Записать сбой ffmpeg в _current_error, откуда его заберёт discord.py

        Свой read() обходит read() базового класса, а именно там discord.py
        вызывает _check_process_returncode. Без этой проверки упавший ffmpeg
        (403 от CDN, битый URL) неотличим от нормального конца трека: after
        получает error=None и очередь молча проматывается до конца
        """
        proc = getattr(self, '_process', None)
        if proc is None:
            return
        # ffmpeg доживает после закрытия пайпа, коду возврата нужно время
        deadline = time.monotonic() + 1.0
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        try:
            self._check_process_returncode()
        except Exception:
            pass

    def _wait_prefill(self) -> None:
        deadline = time.monotonic() + _PREFILL_TIMEOUT
        while (self._buffer.qsize() < _PREFILL_FRAMES
               and self._reader.is_alive()
               and not self._buffer_stop.is_set()
               and time.monotonic() < deadline):
            time.sleep(0.02)

    def wait_ready(self, timeout: float = _PREFILL_TIMEOUT) -> bool:
        """Дождаться, пока в буфере появится стартовый запас. False — звука нет

        Плеер discord.py тактируется абсолютным временем от момента play().
        Если начать с пустым буфером, заминка ffmpeg — а на 403 от CDN он
        переподключается с нарастающей паузой, до нескольких секунд — уходит
        не в задержку старта, а в тишину поверх уже идущего отсчёта, после чего
        плеер досылает накопленное пачкой. Поэтому ждать надо ДО play(),
        а не на первом read()

        Для заготовленного заранее трека буфер уже полон и ожидания нет
        """
        deadline = time.monotonic() + timeout
        while True:
            if self._buffer_stop.is_set():
                return False
            # считаем реальные кадры, а не размер очереди: в ней лежит ещё маркер
            # конца, и поток, не давший НИ ОДНОГО кадра, иначе выглядел бы готовым
            if self._frames_buffered >= _PREFILL_FRAMES:
                return True
            if not self._reader.is_alive():
                # ffmpeg закончился: либо трек короче запаса, либо поток не открылся
                return self._frames_buffered > 0
            if time.monotonic() >= deadline:
                return self._frames_buffered > 0
            time.sleep(0.02)

    def read(self) -> bytes:
        # маркер конца лежит в очереди в единственном экземпляре: без этого флага
        # повторный read() после EOF крутился бы в цикле вечно
        if self._eof_seen:
            return b''
        if not self._prefilled:
            self._prefilled = True
            self._wait_prefill()
        waited = False
        while not self._buffer_stop.is_set():
            try:
                item = self._buffer.get(timeout=0.5)
            except queue.Empty:
                waited = True  # буфер опустел, сеть не успевает за реалтаймом
                continue
            if item is _EOF:
                self._eof_seen = True
                return b''
            if waited:
                self._underruns += 1
            self._frames_sent += 1
            return item
        return b''

    def cleanup(self) -> None:
        if getattr(self, '_underruns', 0):
            _log(f'prebuffer underruns={self._underruns} frames={self._frames_sent} for {self.title!r}')
        self._buffer_stop.set()
        # освобождаем слот, если продюсер завис на put в полный буфер
        try:
            self._buffer.get_nowait()
        except Exception:
            pass
        # убивает ffmpeg -> super().read() в продюсере вернёт b'' -> поток выйдет
        super().cleanup()
        reader = getattr(self, '_reader', None)
        if reader is not None and reader.is_alive() and reader is not threading.current_thread():
            reader.join(timeout=1.0)

    def failure_reason(self) -> str | None:
        """Причина сбоя воспроизведения, либо None

        Опираемся только на код возврата ffmpeg. Считать сбоем «ноль кадров»
        нельзя: при /skip в первые секунды кадров тоже ноль, а cleanup() ещё
        не отработал, потому что discord.py зовёт after до него
        """
        err = getattr(self, '_current_error', None)
        return str(err) if err is not None else None

    @classmethod
    def from_resolved(cls, data: dict, *, start: float = 0.0) -> 'OpusAudioSource':
        """Собрать источник из готовых yt-dlp-подобных данных, поле url — стрим

        start > 0 добавляет -ss перед -i для быстрого поиска при перемотке.

        Маршрут берётся из самих данных (http_proxy), а не параметром: перемотка
        пересобирает источник из того же словаря, и параметр бы потерялся.
        У VK ссылка привязана к адресу запросившего, и поток после перемотки
        ушёл бы мимо прокси
        """
        stream_url = data.get('url')
        if not stream_url:
            raise RuntimeError('Не удалось получить стрим-URL.')
        acodec = (data.get('acodec') or '').lower()
        can_copy = acodec.startswith('opus')
        _log(f'build source (acodec={acodec} abr={data.get("abr")} copy={can_copy} start={start:.1f})')
        before = _BEFORE_OPTIONS
        # Опции ниже понимает только HLS-демуксер: на обычном входе ffmpeg
        # падает с «Option seg_max_retry not found», поэтому флаг обязателен
        if _is_hls(data):
            # -multiple_requests держит соединение под несколько запросов, и на
            # HLS ВКонтакте это сталкивается с переподключениями: связка стоит
            # 10933 мс до первого звука против 1159 без неё, по три прогона
            # вперемежку на одной ссылке. По отдельности обе опции безвредны
            before = before.replace('-multiple_requests 1 ', '')
            # -http_persistent: демуксер переиспользует соединение между
            # сегментами, и на CDN ВКонтакте это даёт «keepalive request
            # failed ... retrying with new connection», 6 отказов на трек
            # против 0.
            # -seg_max_retry по умолчанию 0, то есть сегмент с ошибкой ffmpeg
            # молча ПРОПУСКАЕТ — отсюда и дыры в звуке. Соединение заготовки
            # простаивает, пока играет предыдущий трек, и CDN его закрывает;
            # с перезагрузкой сегмента трек доигрывает целиком
            before = f'-http_persistent 0 -seg_max_retry {_SEG_MAX_RETRY} {before}'
        http_proxy = data.get('http_proxy') or ''
        if http_proxy:
            before = f'-http_proxy {http_proxy} {before}'
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
