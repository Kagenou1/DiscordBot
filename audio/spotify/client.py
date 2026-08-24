"""Инициализация Spotify-клиента, синглтон на процесс

OAuth, а не client-credentials: чтение /playlists/{id}/items на
client-credentials отдаёт 403, Spotify требует пользовательскую авторизацию.
Треки, альбомы и поиск работали бы и без неё

Токен кэшируется spotipy в .cache рядом с проектом, open_browser=False не даёт
уйти в интерактивную авторизацию на машине без дисплея

spotipy держит один requests.Session и один auth manager на клиента и
потокобезопасным не является, обращения к sp идут строго последовательно
"""
import logging

from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth

from private import spotify_client_id, spotify_client_secret, spotify_redirect_uri


_log = logging.getLogger('audio').info

SCOPE = 'playlist-read-private playlist-read-collaborative'


def _build() -> 'Spotify | None':
    if not (spotify_client_id and spotify_client_secret and spotify_redirect_uri):
        print('spotify_* не заданы в private.py — провайдер Spotify выключен.')
        return None
    try:
        auth = SpotifyOAuth(
            client_id=spotify_client_id,
            client_secret=spotify_client_secret,
            redirect_uri=spotify_redirect_uri,
            scope=SCOPE,
            open_browser=False,
        )
        if auth.cache_handler.get_cached_token() is None:
            print('Spotify: нет сохранённого токена в .cache. '
                  'Авторизуйтесь один раз: python -m spotipy.cli или запустите бота с дисплеем.')
            return None
        return Spotify(auth_manager=auth)
    except Exception as exc:
        print(f'spotipy unavailable: {exc!r}')
        return None


sp: 'Spotify | None' = _build()
