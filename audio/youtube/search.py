"""Подбор playable videoId на YT Music со скорингом кандидатов.

Раньше брали results[0] вслепую — из-за этого Spotify-трек мог уехать на
совершенно другую песню (совпало одно слово в названии, артист и длительность
игнорировались). Теперь собираем кандидатов из songs+videos и выбираем лучшего
по близости длительности, совпадению названия и артиста, штрафуя каверы/караоке.
"""
import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache

from .client import ytm


# Сколько кандидатов тянуть из каждого фильтра.
_LIMIT = 6
# Если лучший кандидат из songs уже уверенно хорош — не лезем в videos (экономим запрос).
_EARLY_EXIT = 80.0
# Ниже этого совпадения названия кандидат вообще не рассматривается.
_TITLE_GATE = 0.45

# Маркеры неоригинальных версий: каверы, караоке, инструменталы, лайвы, «ютьюб-каверы».
# Латиницу матчим по словам (множество токенов), CJK — по подстроке (там нет пробелов).
_DERIV_WORDS = frozenset({
    'cover', 'covered', 'karaoke', 'instrumental', 'nightcore', 'remix',
    'remixed', 'reaction', 'acoustic', 'mashup', 'mad',
})
_DERIV_SUBSTR = (
    'カラオケ', 'インスト', 'オフボーカル', 'カバー',
    '歌ってみた', '弾いてみた', '叩いてみた', '踊ってみた',
    'ライブ音響', 'ピアノで',
)


def _norm(s: str) -> str:
    """NFKC + нижний регистр + выкинуть всё, кроме букв/цифр (включая кандзи/кану/хангыль)."""
    s = unicodedata.normalize('NFKC', s or '').lower()
    return re.sub(r'[^\w]', '', s)


def _title_sim(cand_title: str, target_norm: str) -> tuple[float, bool]:
    """(похожесть 0..1, был ли точный сегмент). Режем заголовок на сегменты и берём лучший.

    YT-заголовки часто имеют вид «оригинал - romaji/перевод» или содержат скобки
    с пометками — поэтому сравниваем и куски, и вариант без скобок.
    """
    if not target_norm:
        return 0.0, False
    nobrk = re.sub(r'[\(\[【「『（].*?[\)\]】」』）]', ' ', cand_title)
    parts = re.split(r'\s*[-/／~～|・]\s*', cand_title)
    parts += re.split(r'\s*[-/／~～|・]\s*', nobrk)
    parts += [nobrk, cand_title]
    best, exact = 0.0, False
    for p in parts:
        pn = _norm(p)
        if not pn:
            continue
        if pn == target_norm:
            return 1.0, True
        best = max(best, SequenceMatcher(None, target_norm, pn).ratio())
    return best, exact


def _artist_sim(cand_artist_norm: str, target_norm: str) -> float:
    if not target_norm or not cand_artist_norm:
        return 0.0
    if cand_artist_norm == target_norm:
        return 1.0
    if target_norm in cand_artist_norm or cand_artist_norm in target_norm:
        return 0.8
    return SequenceMatcher(None, target_norm, cand_artist_norm).ratio()


def _is_derivative(cand_title: str) -> bool:
    low = cand_title.lower()
    if any(sub in cand_title for sub in _DERIV_SUBSTR):
        return True
    words = set(re.findall(r'[a-z]+', low))
    return not words.isdisjoint(_DERIV_WORDS)


def _score(item: dict, title_norm: str, artist_norm: str, duration: float) -> float:
    cand_title = item.get('title') or ''
    cand_artist = ', '.join(a.get('name', '') for a in (item.get('artists') or []) if a.get('name'))
    cand_dur = item.get('duration_seconds') or 0
    rtype = item.get('resultType') or ''

    tsim, exact = _title_sim(cand_title, title_norm)
    if tsim < _TITLE_GATE:
        return float('-inf')

    score = tsim * 40.0
    if exact:
        score += 15.0
    if duration and cand_dur:
        score += max(0.0, 30.0 - abs(cand_dur - duration) * 2.0)
    score += _artist_sim(_norm(cand_artist), artist_norm) * 25.0
    if artist_norm and artist_norm in _norm(cand_title):
        score += 8.0  # артист зашит в заголовок (частая ситуация для аниме-OP/реаплоадов)
    if rtype == 'song':
        score += 8.0  # официальное аудио лучше «видео»
    if _is_derivative(cand_title):
        score -= 30.0
    return score


def _best(results: list[dict], title_norm: str, artist_norm: str, duration: float):
    best_item, best_score = None, float('-inf')
    for item in results or []:
        if not item.get('videoId'):
            continue
        sc = _score(item, title_norm, artist_norm, duration)
        if sc > best_score:
            best_score, best_item = sc, item
    return best_item, best_score


def _artist_confident(item: dict, artist_norm: str) -> bool:
    """Совпал ли кандидат с целевым артистом (имя артиста или зашит в заголовок).

    Нужно, чтобы НЕ доверять победителю из songs вслепую: аниме/J-pop часто
    забивают кавер-лейблы (Geek Music, Studio Yuraki…) с точным названием и
    длительностью, но другим артистом. У оригинала артист совпадает.
    """
    if not artist_norm:
        return False
    cand_artist = ', '.join(a.get('name', '') for a in (item.get('artists') or []) if a.get('name'))
    if _artist_sim(_norm(cand_artist), artist_norm) >= 0.6:
        return True
    return artist_norm in _norm(item.get('title') or '')


def ytm_catalog_lookup(title: str, artist: str = '', duration: float = 0.0) -> str | None:
    """Подобрать playable videoId по названию/артисту/длительности через YT Music."""
    if ytm is None or not title:
        return None
    return _lookup(title, artist or '', round(duration or 0.0))


@lru_cache(maxsize=256)
def _lookup(title: str, artist: str, duration: int) -> str | None:
    query = f'{artist} {title}'.strip()
    title_norm = _norm(title)
    artist_norm = _norm(artist)

    songs = _search(query, 'songs')
    best_item, best_score = _best(songs, title_norm, artist_norm, duration)
    # Ранний выход — только если песня из songs уверенно совпала И по артисту.
    # Иначе это может быть кавер-лейбл, забивший songs; смотрим videos (там оригинал).
    if (best_item is not None and best_score >= _EARLY_EXIT
            and _artist_confident(best_item, artist_norm)):
        return best_item['videoId']

    videos = _search(query, 'videos')
    v_item, v_score = _best(videos, title_norm, artist_norm, duration)
    if v_score > best_score:
        best_item, best_score = v_item, v_score

    if best_item is not None:
        return best_item['videoId']
    # совсем ничего не прошло порог названия — отдаём первый попавшийся, чтобы не падать.
    for item in songs + videos:
        if item.get('videoId'):
            return item['videoId']
    return None


def _search(query: str, filt: str) -> list[dict]:
    try:
        return ytm.search(query, filter=filt, limit=_LIMIT) or []
    except Exception as exc:
        print(f'ytmusic search error ({filt}): {exc!r}')
        return []
