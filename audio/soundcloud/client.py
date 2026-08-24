"""yt-dlp под SoundCloud, без extract_flat ради полных метаданных сета

SoundCloud отдаёт title, uploader и thumbnail треков сета inline в одном ответе,
поэтому отказ от flat-режима не добавляет запросов.

soundcloud_oauth_token из private прикладывается заголовком Authorization,
это даёт yt-dlp полные стримы для треков за пейволлом
"""
import yt_dlp as youtube_dl

from private import soundcloud_oauth_token


_ytdl_options = {
    # preview — 30-секундные обрезки за пейволлом SC Go+, иначе yt-dlp выберет их
    'format': 'bestaudio[format_id!*=preview]/bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': False,
    'extract_flat': False,
    'nocheckcertificate': True,
    'ignoreerrors': True,
    'no_warnings': True,
    'quiet': True,
    'socket_timeout': 15,
    'retries': 2,
    'extractor_retries': 2,
}

if soundcloud_oauth_token:
    _ytdl_options['http_headers'] = {
        'Authorization': f'OAuth {soundcloud_oauth_token}',
    }

sc_ytdl = youtube_dl.YoutubeDL(_ytdl_options)
