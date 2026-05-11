"""yt-dlp под SoundCloud: без extract_flat, чтобы метаданные сета приходили полные.

SoundCloud отдаёт title/uploader/thumbnail треков сета inline в одном API-ответе,
поэтому отказ от flat-режима не добавляет лишних запросов.

Если в private.soundcloud_oauth_token указан токен Go+ аккаунта — он прикладывается
к каждому запросу через заголовок Authorization, и yt-dlp получает полные стримы
для треков за пейволлом.
"""
import yt_dlp as youtube_dl

from private import soundcloud_oauth_token


_ytdl_options = {
    # preview-форматы — это 30-секундные обрезки треков за пейволлом SC Go+;
    # явно исключаем, иначе yt-dlp может выбрать их и бот будет играть тишину
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
