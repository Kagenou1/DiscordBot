"""Экстрактор VK Музыки для yt-dlp

Регистрируется в клиенте провайдера через add_info_extractor

Основан на PR yt-dlp #12688, отличия по замерам:
- запросы на vk.ru: сессия выдаётся этому домену, до CDN 44 мс против 184
- плейлисты через al_audio (act=load_section): разметку, которую скребёт
  оригинал, VK больше не отдаёт
- id пользователя приходит снаружи через extractor_args

Нужны куки залогиненного аккаунта. Без них VK отдаёт 31 секунду при полной
заявленной длительности и никак это не помечает. Сессия привязана к адресу
входа: куки с одного IP с другого отвергаются
"""
import re

from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import (
    ExtractorError,
    clean_html,
    int_or_none,
    join_nonempty,
    traverse_obj,
    unescapeHTML,
    url_or_none,
    urlencode_postdata,
)

_DOMAIN = 'https://vk.ru'
# потолок страниц плейлиста, страховка от бесконечного цикла на hasMore
_MAX_PAGES = 100
# без браузерного UA VK отдаёт страницы без нужных маркеров
_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
       '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')


class VKMusicBaseIE(InfoExtractor):
    # алфавит нестандартный: O и 0 переставлены относительно обычного base64
    _B64_CHARS = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMN0PQRSTUVWXYZO123456789+/='

    def _b64_decode(self, enc: str) -> str:
        dec = ''
        e = n = 0
        for c in enc:
            r = self._B64_CHARS.index(c)
            cond = n % 4
            e = 64 * e + r if cond else r
            n += 1
            if cond:
                dec += chr(255 & e >> (-2 * n & 6))
        return dec

    def _unmask_url(self, mask_url: str, vk_id: int) -> str:
        """Снять обфускацию со ссылки на поток

        Перестановка завязана на id пользователя: с чужим id выходит строка,
        которая даже не начинается с http
        """
        if 'audio_api_unavailable' not in mask_url:
            return mask_url
        extra = mask_url.split('?extra=')[1].split('#')
        _, base = self._b64_decode(extra[1]).split(chr(11))
        chars = list(self._b64_decode(extra[0]))
        size = len(chars)
        indexes = [None] * size
        index = int(base) ^ vk_id
        for n in range(size - 1, -1, -1):
            index = (size * (n + 1) ^ index + n) % size
            indexes[n] = index
        for n in range(1, size):
            c = chars[n]
            index = indexes[size - 1 - n]
            chars[n] = chars[index]
            chars[index] = c
        return ''.join(chars)

    def _payload(self, path: str, video_id, data: dict):
        endpoint = f'{_DOMAIN}/{path}.php'
        code, payload = self._download_json(
            endpoint, video_id, data=urlencode_postdata({**data, 'al': 1}),
            headers={
                'User-Agent': _UA,
                'Referer': endpoint,
                'X-Requested-With': 'XMLHttpRequest',
            })['payload']
        if code == '3':
            self.raise_login_required('Нужны куки залогиненного аккаунта VK')
        if code == '8':
            raise ExtractorError(clean_html(payload[0][1:-1]), expected=True)
        return payload

    def _vk_id(self) -> int:
        """id пользователя, чьи куки подставлены

        Задаётся через extractor_args, потому что страница /audios отдаётся
        загрузчику yt-dlp без маркеров id (136 КБ против 293 у requests при
        тех же куках и заголовках). Добывает его клиент провайдера
        """
        raw = self._configuration_arg('user_id', [None], ie_key='vkmusic')[0]
        if not raw:
            raise ExtractorError(
                'Не задан user_id: провайдер VK не прогрет', expected=True)
        return int(raw)

    def _claim_reason(self, meta: list, track_id=None):
        """Отказ правообладателя приходит полем, а не ошибкой запроса"""
        if len(meta) < 13:
            return None
        return traverse_obj(
            self._parse_json(meta[12], track_id, fatal=False), ('claim', 'reason'))

    def _check_claim(self, meta: list, track_id) -> None:
        reason = self._claim_reason(meta, track_id)
        if reason == 'geo':
            self.raise_geo_restricted('Трек недоступен в этом регионе')
        if reason:
            raise ExtractorError(f'Трек недоступен, причина: {reason!r}', expected=True)

    @staticmethod
    def _covers(raw) -> list:
        """Обложки лежат в одном поле через запятую, разными размерами

        Порядок у VK от большего к меньшему, а потребители берут последний
        элемент как лучший — поэтому разворачиваем по возрастанию размера
        """
        urls = [url_or_none(u.strip()) for u in (raw or '').split(',')]
        urls = [u for u in urls if u]

        def px(u):
            m = re.search(r'size=(\d+)x', u)
            return int(m.group(1)) if m else 0

        return [{'url': u} for u in sorted(urls, key=px)]

    def _track_info(self, meta: list, track_id=None) -> dict:
        """Поля трека позиционные, длина массива плавает"""
        size = len(meta)
        title = unescapeHTML(meta[3]) if size > 3 else None
        artist = unescapeHTML(meta[4]) if size > 4 else None
        return {
            'id': f'{meta[1]}_{meta[0]}' if size > 1 and meta[0] and meta[1] else track_id,
            'title': join_nonempty(artist, title, delim=' - '),
            'track': title,
            'artist': artist,
            'uploader': artist,
            'duration': int_or_none(meta[5]) if size > 5 else None,
            'thumbnails': self._covers(meta[14] if size > 14 else ''),
        }

    @staticmethod
    def _access_hash(meta: list) -> str:
        return meta[24] if len(meta) > 24 and meta[24] else ''


class VKMusicTrackIE(VKMusicBaseIE):
    IE_NAME = 'vkmusic:track'
    _VALID_URL = r'''(?x)
                    https?://
                        (?:(?:m|new)\.)?vk\.(?:com|ru)/
                        audio(?P<id>-?\d+_\d+)
                        (?:(?:%2F|_)(?P<hash>[0-9a-f]+))?
                    '''

    def _real_extract(self, url):
        mobj = self._match_valid_url(url)
        track_id = mobj.group('id')
        access_hash = mobj.group('hash') or ''
        vk_id = self._vk_id()

        audio_ids = f'{track_id}_{access_hash}' if access_hash else track_id
        try:
            meta = self._payload('al_audio', track_id, {
                'act': 'reload_audios',
                'audio_ids': audio_ids,
            })[0][0]
        except (ExtractorError, IndexError) as exc:
            # пустой ответ на reload_audios обычно значит отказ правообладателя:
            # причина лежит в метаданных, которых в этом ответе уже нет
            raise ExtractorError(
                f'Трек VK недоступен: {track_id} '
                f'(ограничен правообладателем или регионом)', expected=True) from exc

        self._check_claim(meta, track_id)
        stream = self._unmask_url(meta[2], vk_id)
        if not stream.startswith('http'):
            raise ExtractorError('Ссылка не расшифровалась: неверный user_id',
                                 expected=True)

        return {
            **self._track_info(meta, track_id),
            'formats': [{
                'url': stream,
                'ext': 'm4a',
                'vcodec': 'none',
                'acodec': 'mp3',
                'container': 'm4a_dash',
            }],
        }


class VKMusicPlaylistIE(VKMusicBaseIE):
    IE_NAME = 'vkmusic:playlist'
    _VALID_URL = r'''(?x)
                    https?://
                        (?:(?:m|new)\.)?vk\.(?:com|ru)/
                        (?:
                            music/(?:album|playlist)/|
                            (?:.*[?&](?:act|z)=)?audio_playlist
                        )
                        (?P<full_id>(?P<oid>-?\d+)_(?P<id>\d+))
                        (?:(?:%2F|_|[?&]access_hash=)(?P<hash>[0-9a-f]+))?
                    '''

    def _real_extract(self, url):
        mobj = self._match_valid_url(url)
        full_id = mobj.group('full_id')
        owner_id, playlist_id = mobj.group('oid'), mobj.group('id')
        access_hash = mobj.group('hash') or ''

        entries, head = [], None
        for _ in range(_MAX_PAGES):
            data = self._payload('al_audio', full_id, {
                'act': 'load_section',
                'owner_id': owner_id,
                'playlist_id': playlist_id,
                'access_hash': access_hash,
                'type': 'playlist',
                'offset': len(entries),
                'is_loading_all': 1,
            })[0]
            head = head or data
            tracks = data.get('list') or []
            for meta in tracks:
                info = self._track_info(meta)
                track_id = info.pop('id')
                if not track_id:
                    continue
                # заблокированные правообладателем пропускаем здесь: иначе они
                # встанут в очередь и упрутся уже при воспроизведении
                reason = self._claim_reason(meta, track_id)
                if reason:
                    self.report_warning(
                        f'{track_id}: пропущен, недоступен ({reason})')
                    continue
                # ссылки на поток load_section не отдаёт, поэтому ведём на трек
                tail = self._access_hash(meta)
                entries.append(self.url_result(
                    f'{_DOMAIN}/audio{track_id}' + (f'_{tail}' if tail else ''),
                    VKMusicTrackIE, track_id, info.pop('title'), **info))
            if data.get('hasMore') != '1' or not tracks:
                break

        head = head or {}
        covers = self._covers(head.get('coverUrl'))
        return self.playlist_result(
            entries, full_id,
            unescapeHTML(head.get('title') or ''),
            unescapeHTML(head.get('description') or '') or None,
            uploader=unescapeHTML(head.get('authorName') or '') or None,
            thumbnails=covers)
