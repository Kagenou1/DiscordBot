"""Загрузка плейлиста или альбома Yandex Music через API"""
from ..track import PlaylistInfo, Track
from .client import yandex_client
from .parse import cover_url, ya_track_to_track


# размер пачки для client.tracks(): длинный список id упирается в лимит URL
_BATCH = 100


def yandex_album_info(album_id, *, resolver, limit: int | None = None) -> PlaylistInfo:
    if yandex_client is None:
        raise RuntimeError('Yandex Music клиент недоступен.')
    album = yandex_client.albums_with_tracks(album_id)
    if album is None:
        return PlaylistInfo(tracks=[], kind='album')
    tracks: list[Track] = []
    for volume in (album.volumes or []):
        for ya_track in volume:
            track = ya_track_to_track(ya_track, resolver=resolver, album_id=album_id)
            if track is None:
                continue
            tracks.append(track)
            if limit is not None and len(tracks) >= limit:
                break
        if limit is not None and len(tracks) >= limit:
            break
    artists = getattr(album, 'artists', None) or []
    return PlaylistInfo(
        tracks=tracks,
        title=getattr(album, 'title', '') or '',
        artist=', '.join(a.name for a in artists if getattr(a, 'name', None)),
        url=f'https://music.yandex.ru/album/{album_id}',
        thumbnail=cover_url(getattr(album, 'cover_uri', None)),
        kind='album',
    )


def _materialize(playlist, *, resolver, limit: int | None) -> list[Track]:
    """Развернуть TrackShort плейлиста в Track

    fetch_track() на каждый элемент — это запрос на трек последовательно внутри
    общего 30-секундного таймаута, сотня треков в него не укладывалась.
    Недостающие треки добираются пачками через client.tracks()
    """
    shorts = list(playlist.tracks or [])
    if limit is not None:
        shorts = shorts[:limit]

    # у части элементов объект трека приложен инлайн, их не перезапрашиваем
    missing = [s for s in shorts if getattr(s, 'track', None) is None]
    fetched: dict[str, object] = {}
    for i in range(0, len(missing), _BATCH):
        chunk = missing[i:i + _BATCH]
        ids = [s.track_id for s in chunk]
        try:
            got = yandex_client.tracks(ids) or []
        except Exception as exc:
            print(f'yandex batch fetch failed ({len(ids)} ids): {exc!r}')
            continue
        for ya in got:
            if ya is not None and ya.id is not None:
                fetched[str(ya.id)] = ya

    tracks: list[Track] = []
    for short in shorts:
        ya_track = getattr(short, 'track', None) or fetched.get(str(short.id))
        track = ya_track_to_track(ya_track, resolver=resolver)
        if track is None:
            continue
        tracks.append(track)
    return tracks


def _playlist_meta(playlist, *, fallback_url: str, tracks: list[Track]) -> tuple[str, str, str]:
    title = getattr(playlist, 'title', '') or getattr(playlist, 'name', '') or ''
    thumbnail = ''
    cover = getattr(playlist, 'cover', None)
    # mosaic — автоколлаж из треков, Discord его часто не резолвит
    if cover is not None and getattr(cover, 'type', '') != 'mosaic':
        thumbnail = cover_url(getattr(cover, 'uri', None))
    if not thumbnail and tracks:
        thumbnail = tracks[0].thumbnail
    if not thumbnail:
        thumbnail = cover_url(getattr(playlist, 'og_image', None))
    return title, fallback_url, thumbnail


def yandex_playlist_tracks(kind, user_id, *, resolver, limit: int | None = None) -> PlaylistInfo:
    if yandex_client is None:
        raise RuntimeError('Yandex Music клиент недоступен.')
    playlist = yandex_client.users_playlists(kind, user_id=user_id)
    if playlist is None:
        return PlaylistInfo(tracks=[])
    if isinstance(playlist, list):
        playlist = playlist[0] if playlist else None
    if playlist is None:
        return PlaylistInfo(tracks=[])
    tracks = _materialize(playlist, resolver=resolver, limit=limit)
    title, url, thumbnail = _playlist_meta(
        playlist,
        fallback_url=f'https://music.yandex.ru/users/{user_id}/playlists/{kind}',
        tracks=tracks,
    )
    return PlaylistInfo(tracks=tracks, title=title, url=url, thumbnail=thumbnail)


def yandex_playlist_by_uuid(playlist_uuid: str, *, resolver, limit: int | None = None) -> PlaylistInfo:
    if yandex_client is None:
        raise RuntimeError('Yandex Music клиент недоступен.')
    playlist = yandex_client.playlist(playlist_uuid)
    if playlist is None:
        return PlaylistInfo(tracks=[])
    if isinstance(playlist, list):
        playlist = playlist[0] if playlist else None
    if playlist is None:
        return PlaylistInfo(tracks=[])
    tracks = _materialize(playlist, resolver=resolver, limit=limit)
    title, url, thumbnail = _playlist_meta(
        playlist,
        fallback_url=f'https://music.yandex.ru/playlists/{playlist_uuid}',
        tracks=tracks,
    )
    return PlaylistInfo(tracks=tracks, title=title, url=url, thumbnail=thumbnail)
