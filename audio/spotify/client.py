"""Инициализация Spotify-клиента (синглтон на процесс)."""
import logging

scope = "playlist-read-private playlist-read-collaborative"

_log = logging.getLogger('audio').info


from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
from private import spotify_client_id, spotify_client_secret, spotify_redirect_uri

try:
    sp: 'Spotify | None' = Spotify(auth_manager=SpotifyOAuth(
        client_id=spotify_client_id,
        client_secret=spotify_client_secret,
        redirect_uri=spotify_redirect_uri,
        scope=scope
    ))
except Exception as _exc:
    print(f'spotipy unavailable: {_exc!r}')
    sp = None