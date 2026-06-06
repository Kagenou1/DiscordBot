"""Track — лёгкий описатель трека с одноразовым кэшем последнего extract'а.

Track хранит результат extract'а (если он только что был сделан) и при
следующем make_source использует его, минуя повторный сетевой запрос.
Стрим-URL у YouTube живёт ~6 ч; мы переиспользуем кэш до 30 минут —
покрывает «добавил пачку треков и слушает по очереди» без повторных
extract_info.
"""
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

if TYPE_CHECKING:
    from .source import OpusAudioSource


_RESOLVED_TTL = 1800.0


Resolver = Callable[..., Awaitable['OpusAudioSource']]


@dataclass
class PlaylistInfo:
    """Метаданные плейлиста/альбома для отображения при добавлении в очередь."""
    tracks: list['Track']
    title: str = ''
    url: str = ''
    thumbnail: str = ''
    kind: str = 'playlist'  # 'playlist' или 'album'


@dataclass
class Track:
    url: str
    title: str
    artist: str = ''
    thumbnail: str = ''
    duration: float = 0.0  # секунды; нужно для скоринга YT-эквивалента при resolve Spotify->YT
    resolver: Optional[Resolver] = field(default=None, repr=False)
    _resolved: Optional[dict] = field(default=None, repr=False)
    _resolved_at: float = field(default=0.0, repr=False)
    _fallback_tried: bool = field(default=False, repr=False)

    def cache_resolved(self, data: dict) -> None:
        self._resolved = data
        self._resolved_at = time.monotonic()

    async def make_source(self, *, loop=None, timeout=30) -> 'OpusAudioSource':
        from .source import OpusAudioSource
        cached = self._resolved
        if cached is not None and (time.monotonic() - self._resolved_at) < _RESOLVED_TTL:
            self._resolved = None
            try:
                return OpusAudioSource.from_resolved(cached)
            except Exception as exc:
                print(f'cached resolve failed for {self.title!r}: {exc!r} — re-extracting')
        if self.resolver is None:
            raise RuntimeError('Track has no resolver attached.')
        return await self.resolver(self, loop=loop, timeout=timeout)
