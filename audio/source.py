"""ffmpeg-обёртка для воспроизведения готового стрим-URL."""
import logging
import os
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
_BEFORE_OPTIONS = (
    '-nostdin '
    '-reconnect 1 -reconnect_streamed 1 -reconnect_at_eof 1 '
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


ffmpeg_options = {
    'before_options': _BEFORE_OPTIONS,
    'options': _OUTPUT_OPTIONS,
}


class OpusAudioSource(discord.FFmpegOpusAudio):
    """FFmpegOpusAudio с метаданными трека (title/url/duration/thumbnail)."""

    def __init__(self, source, *, data, codec=None, executable=None, **kwargs):
        super().__init__(source, codec=codec, executable=executable, **kwargs)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    def from_resolved(cls, data: dict) -> 'OpusAudioSource':
        """Собрать источник из готовых yt-dlp-подобных данных (поле url — стрим)."""
        stream_url = data.get('url')
        if not stream_url:
            raise RuntimeError('Не удалось получить стрим-URL.')
        acodec = (data.get('acodec') or '').lower()
        can_copy = acodec.startswith('opus')
        _log(f'build source (acodec={acodec} abr={data.get("abr")} copy={can_copy})')
        return cls(
            stream_url,
            data=data,
            codec='copy' if can_copy else None,
            bitrate=_OPUS_BITRATE,
            executable=ffmpeg_path,
            **ffmpeg_options,
        )
