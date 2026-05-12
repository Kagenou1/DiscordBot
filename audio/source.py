"""ffmpeg-обёртка для воспроизведения готового стрим-URL."""
import logging

import discord

from private import ffmpeg_path


_log = logging.getLogger('audio').info


ffmpeg_options = {
    'before_options': (
        '-reconnect 1 -reconnect_streamed 1 -reconnect_at_eof 1 '
        '-reconnect_delay_max 5 -rw_timeout 5000000 -multiple_requests 1 '
        '-thread_queue_size 1024'
    ),
    'options': '-vn',
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
            executable=ffmpeg_path,
            **ffmpeg_options,
        )
