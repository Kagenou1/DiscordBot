"""Инициализация yt-dlp и ytmusicapi (синглтоны на процесс)."""
import logging
import os

import yt_dlp as youtube_dl

from private import deno_path


_log = logging.getLogger('audio').info


_deno_dir = os.path.dirname(deno_path)
if _deno_dir and _deno_dir not in os.environ.get('PATH', '').split(os.pathsep):
    os.environ['PATH'] = _deno_dir + os.pathsep + os.environ.get('PATH', '')


try:
    from ytmusicapi import YTMusic
    ytm: 'YTMusic | None' = YTMusic()
except Exception as _exc:
    print(f'ytmusicapi unavailable: {_exc!r}')
    ytm = None


_ytdl_format_options = {
    'format': 'bestaudio[acodec=opus]/bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': False,
    'extract_flat': 'in_playlist',
    'nocheckcertificate': True,
    'ignoreerrors': True,
    'no_warnings': True,
    'quiet': True,
    'default_search': 'ytsearch',
    'socket_timeout': 15,
    'retries': 2,
    'extractor_retries': 2,
    # 'cookiefile': 'cookies.txt',
    'concurrent_fragment_downloads': 4,
    'remote_components': ['ejs:github'],
    'extractor_args': {
        'youtube': {
            'player_client': ['default', 'web', 'mweb', 'android', 'ios'],
        },
    },
}
youtube_dl.utils.bug_reports_message = lambda *args, **kwargs: ''
ytdl = youtube_dl.YoutubeDL(_ytdl_format_options)
