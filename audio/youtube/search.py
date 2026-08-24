"""Подбор playable videoId на YT Music со скорингом кандидатов

results[0] вслепую уводил Spotify-трек на другую песню: совпадало одно слово
в названии, артист и длительность игнорировались. Кандидаты собираются из
songs и videos, лучший выбирается по близости длительности, совпадению
названия и артиста, каверы и караоке штрафуются
"""
import json
import re
import threading
import time
import unicodedata
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from difflib import SequenceMatcher

from .client import ytm


# сколько кандидатов тянуть из каждого фильтра
_LIMIT = 6
# ниже этого совпадения названия кандидат не рассматривается
_TITLE_GATE = 0.45
# Потолок времени на поиск артиста. Это бонус к скорингу, а не необходимость:
# без него работает строковое сравнение, поэтому задерживать воспроизведение он
# не вправе. В спокойном состоянии запрос стоит ~500 мс и в бюджет влезает
# несколько попыток; в плохую погоду будет ровно одна
_ARTIST_LOOKUP_BUDGET = 1.5

# пауза между попытками: без неё мгновенно падающий ответ крутит цикл вхолостую
_ARTIST_RETRY_PAUSE = 0.25

# Кэш имя артиста -> browseId, переживающий перезапуск. ID у артиста не меняется,
# а поиск нестабилен и стоит до полутора секунд на критическом пути первого трека:
# у QUEEN BEE он резолвится примерно раз из пяти. Платим один раз за всю жизнь
_ARTIST_CACHE_PATH = Path(__file__).resolve().parents[2] / 'log' / '.artist-ids.json'
_artist_ids: dict[str, str] = {}
_artist_cache_lock = threading.Lock()


def _load_artist_cache() -> None:
    try:
        data = json.loads(_ARTIST_CACHE_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return
    if isinstance(data, dict):
        _artist_ids.update({k: v for k, v in data.items() if isinstance(v, str)})


def _remember_artist(name: str, artist_id: str) -> None:
    with _artist_cache_lock:
        _artist_ids[name] = artist_id
        try:
            _ARTIST_CACHE_PATH.parent.mkdir(exist_ok=True)
            tmp = _ARTIST_CACHE_PATH.with_suffix('.tmp')
            tmp.write_text(json.dumps(_artist_ids, ensure_ascii=False), encoding='utf-8')
            tmp.replace(_ARTIST_CACHE_PATH)  # атомарно: параллельные резолвы не рвут файл
        except OSError as exc:
            print(f'artist cache write failed: {exc!r}')


_load_artist_cache()

# Запасной путь через каталог артиста. Требования жёстче обычных намеренно:
# при пороге 0.45 'ADAMAS' против 'Dream' даёт 0.55, и каталог подменял верно
# найденную песню другой песней того же исполнителя. Пускаем только точное
# совпадение сегмента названия либо сходство от 0.7 при близкой длительности
_CATALOG_TITLE_GATE = 0.7
_CATALOG_DUR_WINDOW = 8.0
# каталог поднимается редко, поэтому бюджет щедрее, чем у поиска артиста
_CATALOG_BUDGET = 3.0
_catalogs: dict[str, tuple] = {}
_full_catalogs: dict[str, list] = {}
# штраф упорядочивает производные версии между собой; выбор типа версии — в _best
_DERIV_PENALTY = 30.0

# Маркеры неоригинальных версий, сведённые к категориям. Категория важна сама
# по себе: если просили инструментал, версия From THE FIRST TAKE его не заменяет,
# хотя обе — не студийный оригинал. Латиница матчится по токенам, CJK по подстроке
_DERIV_WORDS = {
    'cover': 'cover', 'covered': 'cover', 'karaoke': 'karaoke',
    'instrumental': 'instrumental', 'nightcore': 'nightcore', 'remix': 'remix',
    'remixed': 'remix', 'reaction': 'reaction', 'acoustic': 'acoustic',
    'mashup': 'mashup', 'mad': 'mad',
}
_DERIV_SUBSTR = {
    'カラオケ': 'karaoke', 'インスト': 'instrumental', 'オフボーカル': 'offvocal',
    'カバー': 'cover', '歌ってみた': 'cover', '弾いてみた': 'cover',
    '叩いてみた': 'cover', '踊ってみた': 'cover',
    'ライブ音響': 'live', 'ピアノで': 'piano',
    '武道館': 'live', 'コンサート': 'live', 'ライブver': 'live',
}
# слитные формы вроде Off Vocal / sped up: не отдельные токены, матчим по _norm-подстроке
_DERIV_NORM_SUBSTR = {
    'offvocal': 'offvocal', 'vocaloff': 'offvocal',
    'spedup': 'spedup', 'slowedreverb': 'slowedreverb',
    'thefirsttake': 'firsttake', 'tvsize': 'tvsize', 'tvver': 'tvsize',
    'livever': 'live', 'liveversion': 'live',
    # концертные записи проходили за оригинал: у ReoNa "SWEET HURT" в заголовке
    # ONE-MAN Concert, у FLOW "WORLD END" — LIVE at 幕張メссе, а из живых пометок
    # были только японские コンサート и 武道館. Голое 'concert' не берём: оно
    # подстрока от 'concerto' и пометило бы классику
    'oneman': 'live', 'liveat': 'live', 'livetour': 'live',
    # слитные формы каверов: 【coverby叶羽】 и караоке-формула
    # "(Originally Performed by ...)" пробелами не разделяются на токены
    'coverby': 'cover', 'originallyperformedby': 'karaoke',
    # официальный канал артиста выкладывает не только песню: у таких роликов
    # название и длительность совпадают с оригиналом идеально, поэтому очками
    # они не отсеиваются — только отдельным видом версии
    'guidevideo': 'guide', 'dancepractice': 'dance',
    'performancemovie': 'performance', 'behindthescenes': 'behind',
    'makingof': 'behind',
}

# 'live' отдельным словом ловил бы «Live Your Life», поэтому только в скобках-пометке
_LIVE_TAG = re.compile(r'[(\[【]\s*live(?![a-z])', re.IGNORECASE)


# скобки-пометки; группа захватывает содержимое, чтобы им тоже можно было сравнивать
_BRACKETED = re.compile(r'[\(\[【「『（]([^\)\]】」』）]*)[\)\]】」』）]')


# Дефис, слитный с началом слова, YT Music читает как «исключить слово», а в
# названиях это обычная пунктуация. По «Mili Entertainment -Goblin Slayer II
# Opening Theme» выдача не содержит ни одной записи со словом Goblin; по тому же
# запросу без дефиса официальная запись первая. Так же ломалась и пометка
# версии: «-Instrumental-» исключал инструментал из выдачи
_QUERY_OPERATOR = re.compile(r'(?<![^\s])-+(?=\S)')


def _query(*parts: str) -> str:
    """Строка запроса к YT Music без поисковых операторов"""
    joined = _QUERY_OPERATOR.sub(' ', ' '.join(p for p in parts if p))
    return re.sub(r'\s+', ' ', joined).strip()


def _norm(s: str) -> str:
    """NFKC, нижний регистр, только буквы и цифры, включая кандзи, кану и хангыль"""
    s = unicodedata.normalize('NFKC', s or '').lower()
    return re.sub(r'[^\w]', '', s)


def _title_sim(cand_title: str, target_norm: str) -> tuple[float, bool]:
    """(похожесть 0..1, был ли точный сегмент)

    YT-заголовки часто имеют вид «оригинал - romaji» или содержат скобки с пометками,
    поэтому сравниваются и сегменты, и вариант без скобок
    """
    if not target_norm:
        return 0.0, False
    nobrk = _BRACKETED.sub(' ', cand_title)
    parts = re.split(r'\s*[-/／~～|・]\s*', cand_title)
    parts += re.split(r'\s*[-/／~～|・]\s*', nobrk)
    # Отдельно по дефису с пробелами: YT Music дописывает романизацию через
    # « - », а разделитель ・ стоит ВНУТРИ катаканы, и общий разбор рассыпает
    # «エクストラ・マジック・アワー - Extra Magic Hour» на три куска, из которых
    # целого названия не остаётся. Части только добавляются, сходство берётся
    # максимумом, поэтому хуже стать не может
    parts += re.split(r'\s+[-–—]\s+', cand_title)
    parts += re.split(r'\s+[-–—]\s+', nobrk)
    parts += [nobrk, cand_title]
    # содержимое скобок — тоже кандидат: японские официальные заголовки кладут
    # имя песни именно в 『』, и вариант без скобок его теряет
    parts += _BRACKETED.findall(cand_title)
    best, exact = 0.0, False
    for p in parts:
        pn = _norm(p)
        if not pn:
            continue
        if pn == target_norm:
            return 1.0, True
        best = max(best, SequenceMatcher(None, target_norm, pn).ratio())
    return best, exact


# Заблокированный запрос не отвечает 6-8 секунд: пограничный слой Google
# намеренно держит соединение, прежде чем отдать страницу "Sorry...". Успешный
# укладывается в ~400 мс. Разрыв чистый, поэтому молчание дольше секунды почти
# наверняка блокировка, и её можно обгонять дублем, не дожидаясь ответа.
# Замер на 40 треках: последовательно 177с и худший случай 17.4с, параллельно
# с дублем 37с и 4.3с. Лишних запросов почти нет (135 против 131): дубль не
# добавляет работу, а заменяет ожидание — заблокированный запрос всё равно
# пришлось бы повторять
_HEDGE_DELAY = 1.2
_HEDGE_ATTEMPTS = 3
# потолок на весь сбор. Дубли ускоряют обычный случай, но если молчат все
# попытки сразу, подбор не должен висеть: лучше отдать неполную выдачу.
# Однажды повтор без потолка по времени уже дал 16 секунд до звука
_GATHER_BUDGET = 9.0
# Потоки заняты ожиданием сети, а не счётом, поэтому их берём с запасом:
# один подбор занимает до пяти заданий, а гильдий может играть несколько разом.
# Тесный пул опаснее широкого — задания вставали бы в очередь, а дубль по
# таймеру считал бы это молчанием сервера
_POOL = ThreadPoolExecutor(max_workers=32, thread_name_prefix='ytm')


class _Empty(Exception):
    """Пустая выдача без ошибки: у YT Music это тоже симптом сбоя, а не ответ"""


def _gather(jobs: dict) -> dict:
    """Выполнить независимые запросы разом, дублируя те, что молчат

    Один цикл ожидания на все задания, вложенных пулов нет: иначе задачи пула
    ждали бы других задач того же пула и он вставал бы намертво
    """
    pending: dict = {}
    launched: dict = {}
    results: dict = {}
    for key, fn in jobs.items():
        pending[_POOL.submit(fn)] = key
        launched[key] = 1

    deadline = time.monotonic() + _GATHER_BUDGET
    while pending:
        left = deadline - time.monotonic()
        if left <= 0:
            break
        done, _ = wait(set(pending), timeout=min(_HEDGE_DELAY, left), return_when=FIRST_COMPLETED)
        if not done:
            added = False
            # дублируем только задания, которые реально начались: стоящее
            # в очереди пула молчит не потому, что сервер не отвечает, и
            # дубль лишь углубил бы ту же очередь
            started = {key for fut, key in pending.items()
                       if key not in results and (fut.running() or fut.done())}
            for key in started:
                if launched[key] < _HEDGE_ATTEMPTS:
                    pending[_POOL.submit(jobs[key])] = key
                    launched[key] += 1
                    added = True
            if added:
                continue
            # дубли исчерпаны, ждём хоть какого-то ответа, но не дольше потолка
            done, _ = wait(set(pending), timeout=max(0.0, deadline - time.monotonic()),
                           return_when=FIRST_COMPLETED)
            if not done:
                break

        for fut in done:
            key = pending.pop(fut, None)
            if key is None or key in results:
                continue
            try:
                results[key] = fut.result()
            except _Empty:
                pass
            except Exception as exc:
                print(f'ytmusic {key} error: {exc!r}')
            else:
                continue
            if launched[key] < _HEDGE_ATTEMPTS:
                pending[_POOL.submit(jobs[key])] = key
                launched[key] += 1

        # дубли уже отвеченных ключей больше не ждём, пусть доигрывают в пуле
        for fut, key in list(pending.items()):
            if key in results:
                pending.pop(fut, None)
    return results


def _artist_id(name: str) -> str | None:
    """browseId артиста в YT Music по его имени

    Spotify пишет японских исполнителей иероглифами, YouTube — латиницей, и
    строковое сравнение эту пару не связывает: 藤川千愛 против Chiai Fujikawa даёт
    сходство 0.0. У 12% библиотеки бонус за артиста из-за этого не начислялся
    вовсе, и скоринг работал вслепую по названию и длительности.

    Поиск артиста нестабилен: замер дал QUEEN BEE 1 успех из 5 при 5 из 5 у
    остальных имён, причём пустая выдача приходит без ошибки, а ytm.search
    изредка отдаёт не-JSON. Поэтому повторяем, но строго в рамках бюджета
    """
    if ytm is None or not name:
        return None
    known = _artist_ids.get(name)
    if known is not None:
        return known
    deadline = time.monotonic() + _ARTIST_LOOKUP_BUDGET
    while True:
        try:
            got = _artist_id_call(name)
        except _Empty:
            got = None
        except Exception as exc:
            print(f'ytmusic artist lookup error ({name!r}): {exc!r}')
        else:
            if got:
                _remember_artist(name, got)
                return got
        if time.monotonic() >= deadline:
            return None
        time.sleep(_ARTIST_RETRY_PAUSE)


def _artist_name(item: dict) -> str:
    return item.get('artist') or item.get('title') or ''


def _artist_id_call(name: str) -> str:
    """Один запрос за browseId

    Пустая выдача считается сбоем, а не ответом: замер дал QUEEN BEE 1 успех
    из 5 при 5 из 5 у остальных имён, причём пустота приходит без исключения
    """
    results = ytm.search(_query(name), filter='artists', limit=3) or []
    # Точное совпадение имени важнее порядка выдачи. На короткие имена YouTube
    # первым отдаёт чужого: по "FLOW" — бразильского Flow Firme с каталогом из
    # Le Hice Caso Al Corazon, тогда как настоящий FLOW идёт вторым и у него
    # в каталоге нужный WORLD END. По "Ray" первым идёт Ray Charles
    target = _norm(name)
    for r in results:
        if r.get('browseId') and _norm(_artist_name(r)) == target:
            return r['browseId']
    # Без точного совпадения берём первого: сравнивать ромадзи с кандзи
    # бесполезно, 藤川千愛 против Chiai Fujikawa даёт 0.0, а это верная пара
    for r in results:
        if r.get('browseId'):
            return r['browseId']
    raise _Empty


def _has_artist(item: dict, artist_id: str) -> bool:
    return artist_id in {a.get('id') for a in (item.get('artists') or []) if a.get('id')}


def _artist_songs(artist_id: str):
    """(короткий список песен, browseId полного) — оба из одного запроса get_artist

    Кэшируется только непустой ответ: get_artist подвержен той же нестабильности,
    что и поиск, а закэшированная неудача выключила бы запасной путь до перезапуска
    """
    cached = _catalogs.get(artist_id)
    if cached:
        return cached
    deadline = time.monotonic() + _CATALOG_BUDGET
    while True:
        try:
            info = ytm.get_artist(artist_id)
            block = info.get('songs') or {}
            pair = (block.get('results') or [], block.get('browseId'))
            if pair[0] or pair[1]:
                _catalogs[artist_id] = pair
                return pair
        except Exception as exc:
            print(f'ytmusic artist catalog error ({artist_id}): {exc!r}')
        if time.monotonic() >= deadline:
            return [], None
        time.sleep(_ARTIST_RETRY_PAUSE)


def _full_catalog(browse_id: str) -> list:
    """Полный список песен артиста — отдельный запрос, только если короткий не помог"""
    cached = _full_catalogs.get(browse_id)
    if cached:
        return cached
    try:
        tracks = ytm.get_playlist(browse_id, limit=300).get('tracks') or []
    except Exception as exc:
        print(f'ytmusic full catalog error ({browse_id}): {exc!r}')
        return []
    if tracks:
        _full_catalogs[browse_id] = tracks
    return tracks


def _from_catalog(artist_id, title_norm, artist_norm, duration, want):
    """Запись из каталога артиста, когда поиск её не отдал

    У QUEEN BEE «メフィスト - Mephisto» лежит в каталоге, но поиск по
    «QUEEN BEE メフィスト» возвращает только off vocal ver., и бот играл запись
    другого исполнителя с тем же названием.

    Сначала короткий список из get_artist: он приходит тем же запросом, и нужная
    запись часто уже там. Полный список — лишний запрос почти на секунду
    """
    short, browse = _artist_songs(artist_id)
    hit = _pick_from(short, title_norm, artist_norm, duration, want, artist_id)
    if hit is not None or not browse:
        return hit
    return _pick_from(_full_catalog(browse), title_norm, artist_norm, duration, want, artist_id)


def _pick_from(items, title_norm, artist_norm, duration, want, artist_id):
    """Лучшая запись из готового списка при жёстких требованиях к совпадению"""
    best = None
    for item in items or []:
        if not item.get('videoId'):
            continue
        if _deriv_markers(item.get('title') or '') != want:
            continue
        cand_dur = item.get('duration_seconds') or 0
        if duration and cand_dur and abs(cand_dur - duration) > _CATALOG_DUR_WINDOW:
            continue
        sim, exact = _title_sim(item.get('title') or '', title_norm)
        if not exact and sim < _CATALOG_TITLE_GATE:
            continue
        score = _score(item, title_norm, artist_norm, duration, artist_id)
        if best is None or score > best[1]:
            best = (item, score)
    return best[0] if best else None


def _artist_match(item: dict, artist_norm: str, artist_id: str | None) -> float:
    """Совпадение артиста 0..1: точное по ID, иначе по строке"""
    if artist_id:
        ids = {a.get('id') for a in (item.get('artists') or []) if a.get('id')}
        if artist_id in ids:
            return 1.0
    cand = ', '.join(a.get('name', '') for a in (item.get('artists') or []) if a.get('name'))
    return _artist_sim(_norm(cand), artist_norm)


def _artist_sim(cand_artist_norm: str, target_norm: str) -> float:
    if not target_norm or not cand_artist_norm:
        return 0.0
    if cand_artist_norm == target_norm:
        return 1.0
    if target_norm in cand_artist_norm or cand_artist_norm in target_norm:
        return 0.8
    return SequenceMatcher(None, target_norm, cand_artist_norm).ratio()


def _deriv_markers(cand_title: str) -> frozenset:
    """Категории альтернативной версии, найденные в названии

    Возвращается именно набор категорий, а не флаг: инструментал и запись
    From THE FIRST TAKE обе не студийный оригинал, но взаимозаменяемыми не являются
    """
    found = set()
    for sub, kind in _DERIV_SUBSTR.items():
        if sub in cand_title:
            found.add(kind)
    norm = _norm(cand_title)
    for sub, kind in _DERIV_NORM_SUBSTR.items():
        if sub in norm:
            found.add(kind)
    if _LIVE_TAG.search(cand_title):
        found.add('live')
    for word in re.findall(r'[a-z]+', cand_title.lower()):
        kind = _DERIV_WORDS.get(word)
        if kind:
            found.add(kind)
    return frozenset(found)


def _is_derivative(cand_title: str) -> bool:
    return bool(_deriv_markers(cand_title))


def _strip_markers(title: str) -> str:
    """Название без пометок версии

    YT Music по строке «ClariS ヒトリゴト -Instrumental-» не находит сам инструментал
    и отдаёт каверы с караоке, а по чистому «ClariS ヒトリゴト» он в выдаче есть.
    Поэтому при запросе с пометкой ищем ещё и по базовому названию
    """
    base = _BRACKETED.sub(' ', title)
    parts = [p for p in re.split(r'\s*[-–—]\s*', base)
             if p.strip() and not _deriv_markers(p)]
    return ' '.join(parts).strip() or title


def _score(item: dict, title_norm: str, artist_norm: str, duration: float,
           artist_id: str | None = None) -> float:
    cand_title = item.get('title') or ''
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
    score += _artist_match(item, artist_norm, artist_id) * 25.0
    if artist_norm and artist_norm in _norm(cand_title):
        score += 8.0  # артист зашит в заголовок, частое для аниме-OP и реаплоадов
    if rtype == 'song':
        score += 8.0  # официальное аудио лучше видео
    if _is_derivative(cand_title):
        score -= _DERIV_PENALTY
    return score


_NOTHING = (None, float('-inf'))


def _best(results: list[dict], title_norm: str, artist_norm: str, duration: float,
          *, want_markers: frozenset = frozenset(), artist_id: str | None = None):
    """Три уровня предпочтения, каждый как (item, score)

    Вид версии вынесен из очков в отдельное измерение: инструментал или кавер той
    же песни совпадает по названию, артисту и длительности почти идеально, поэтому
    штраф _DERIV_PENALTY его не перебивает — у ClariS «コイセカイ (Instrumental)»
    выходило 88 против 66 у оригинала. Штраф упорядочивает кандидатов внутри уровня

    1. exact — тот же набор категорий, что у запроса: просили инструментал, нашли
       инструментал; просили оригинал, нашли оригинал
    2. same  — совпал только факт «оригинал или нет», категории разные
    3. any   — лучший по очкам, если ничего подходящего нет вовсе
    """
    exact_best = same_best = any_best = _NOTHING
    want_deriv = bool(want_markers)
    for item in results or []:
        if not item.get('videoId'):
            continue
        sc = _score(item, title_norm, artist_norm, duration, artist_id)
        if sc == float('-inf'):
            continue
        markers = _deriv_markers(item.get('title') or '')
        if sc > any_best[1]:
            any_best = (item, sc)
        if bool(markers) == want_deriv and sc > same_best[1]:
            same_best = (item, sc)
        if markers == want_markers and sc > exact_best[1]:
            exact_best = (item, sc)
    return exact_best, same_best, any_best


def ytm_catalog_lookup(title: str, artist: str = '', duration: float = 0.0) -> str | None:
    """Подобрать playable videoId по названию, артисту и длительности"""
    if ytm is None or not title:
        return None
    return _lookup(title, artist or '', round(duration or 0.0))


_LOOKUP_CACHE_MAX = 256
_lookup_cache: dict[tuple, str] = {}


def _lookup(title: str, artist: str, duration: int) -> str | None:
    """Подбор с кэшем ТОЛЬКО удачных ответов

    lru_cache здесь запоминал и None. А None получается, когда YouTube
    заблокировал запросы: трек становился неподбираемым до перезапуска
    процесса, хотя со второй попытки нашёлся бы. Тот же инвариант уже
    соблюдён в _artist_ids и _catalogs
    """
    key = (title, artist, duration)
    hit = _lookup_cache.get(key)
    if hit is not None:
        return hit
    got = _lookup_uncached(title, artist, duration)
    if got:
        if len(_lookup_cache) >= _LOOKUP_CACHE_MAX:
            _lookup_cache.pop(next(iter(_lookup_cache)), None)
        _lookup_cache[key] = got
    return got


_lookup.cache_clear = _lookup_cache.clear


def _lookup_uncached(title: str, artist: str, duration: int) -> str | None:
    query = _query(artist, title)
    title_norm = _norm(title)
    artist_norm = _norm(artist)
    # что именно просили: пустой набор — студийный оригинал
    want = _deriv_markers(title)
    # songs и videos спрашиваем всегда. Ранний выход по songs здесь был: он
    # экономил один запрос, но систематически возвращал не ту запись — сначала
    # инструментал ClariS, потом версию From THE FIRST TAKE, — потому что
    # официальный клип лежит в videos и до сравнения дело не доходило
    jobs = {'songs': lambda: _search_call(query, 'songs'),
            'videos': lambda: _search_call(query, 'videos')}
    # ID связывает 藤川千愛 с Chiai Fujikawa там, где строки не связываются
    cached_id = _artist_ids.get(artist) if artist else None
    if artist and cached_id is None and ytm is not None:
        jobs['artist lookup'] = lambda: _artist_id_call(artist)
    # запрос с пометкой версии сбивает поиск: нужную запись он часто не находит,
    # поэтому добираем кандидатов по базовому названию
    base = _query(artist, _strip_markers(title)) if want else query
    if want and base != query:
        jobs['songs base'] = lambda: _search_call(base, 'songs')
        jobs['videos base'] = lambda: _search_call(base, 'videos')

    # все запросы независимы, поэтому уходят разом: последовательно они
    # складывали задержки, а на блокировке одного ждали его шесть секунд впустую
    got = _gather(jobs)

    artist_id = cached_id
    if got.get('artist lookup'):
        artist_id = got['artist lookup']
        _remember_artist(artist, artist_id)

    songs = got.get('songs') or []
    videos = got.get('videos') or []
    if got.get('songs base'):
        songs = _merge(songs, got['songs base'])
    if got.get('videos base'):
        videos = _merge(videos, got['videos base'])

    tiers = zip(_best(songs, title_norm, artist_norm, duration,
                      want_markers=want, artist_id=artist_id),
                _best(videos, title_norm, artist_norm, duration,
                      want_markers=want, artist_id=artist_id))
    winner = None
    for from_songs, from_videos in tiers:
        candidate = max(from_songs, from_videos, key=lambda r: r[1])
        if candidate[0] is not None:
            winner = candidate[0]
            break

    # поиск не отдал ни одной записи нужного исполнителя — спросим его каталог
    if artist_id and (winner is None or not _has_artist(winner, artist_id)):
        alt = _from_catalog(artist_id, title_norm, artist_norm, duration, want)
        if alt is not None:
            return alt['videoId']

    if winner is not None:
        return winner['videoId']

    # ничего не прошло порог названия, отдаём первый попавшийся вместо падения
    for item in songs + videos:
        if item.get('videoId'):
            return item['videoId']
    return None


def _merge(*groups: list[dict]) -> list[dict]:
    """Склеить выдачи, сохранив порядок и выбросив повторы по videoId"""
    seen, out = set(), []
    for group in groups:
        for item in group or []:
            vid = item.get('videoId')
            if vid and vid in seen:
                continue
            if vid:
                seen.add(vid)
            out.append(item)
    return out


def _search_call(query: str, filt: str) -> list[dict]:
    """Один поиск. Пустая выдача — законный ответ и не повторяется:
    повтор нужен только для сбоя, то есть блокировки"""
    return ytm.search(query, filter=filt, limit=_LIMIT) or []


def _search(query: str, filt: str) -> list[dict]:
    key = f'search ({filt})'
    return _gather({key: lambda: _search_call(query, filt)}).get(key) or []
