"""Загрузка плейлиста/альбома Yandex Music через API."""
from ..track import Track
from .client import yandex_client
from .parse import ya_track_to_track


def yandex_album_tracks(album_id, *, resolver, limit: int | None = None) -> list[Track]:
    if yandex_client is None:
        raise RuntimeError('Yandex Music клиент недоступен.')
    album = yandex_client.albums_with_tracks(album_id)
    if album is None:
        return []
    tracks: list[Track] = []
    for volume in (album.volumes or []):
        for ya_track in volume:
            track = ya_track_to_track(ya_track, resolver=resolver, album_id=album_id)
            if track is None:
                continue
            tracks.append(track)
            if limit is not None and len(tracks) >= limit:
                return tracks
    return tracks


def _materialize(playlist, *, resolver, limit: int | None) -> list[Track]:
    tracks: list[Track] = []
    for short in (playlist.tracks or []):
        ya_track = getattr(short, 'track', None) or short.fetch_track()
        track = ya_track_to_track(ya_track, resolver=resolver)
        if track is None:
            continue
        tracks.append(track)
        if limit is not None and len(tracks) >= limit:
            return tracks
    return tracks


def yandex_playlist_tracks(kind, user_id, *, resolver, limit: int | None = None) -> list[Track]:
    if yandex_client is None:
        raise RuntimeError('Yandex Music клиент недоступен.')
    playlist = yandex_client.users_playlists(kind, user_id=user_id)
    if playlist is None:
        return []
    if isinstance(playlist, list):
        playlist = playlist[0] if playlist else None
    if playlist is None:
        return []
    return _materialize(playlist, resolver=resolver, limit=limit)


def yandex_playlist_by_uuid(playlist_uuid: str, *, resolver, limit: int | None = None) -> list[Track]:
    if yandex_client is None:
        raise RuntimeError('Yandex Music клиент недоступен.')
    playlist = yandex_client.playlist(playlist_uuid)
    if playlist is None:
        return []
    return _materialize(playlist, resolver=resolver, limit=limit)
