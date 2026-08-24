"""Скоринг кандидатов YT Music

Тесты фиксируют поведение подбора, чтобы правки в других местах его не сдвинули
"""
import threading

import pytest

from audio.youtube import search


def test_norm_strips_everything_but_alnum():
    assert search._norm('Ｈｅｌｌｏ, World! (2024)') == 'helloworld2024'
    assert search._norm('残酷な天使のテーゼ') == '残酷な天使のテーゼ'
    assert search._norm(None) == ''


def test_title_sim_exact_segment():
    sim, exact = search._title_sim('Cruel Angel - 残酷な天使のテーゼ', search._norm('残酷な天使のテーゼ'))
    assert exact is True and sim == 1.0


def test_title_sim_ignores_bracket_notes():
    sim, _ = search._title_sim('Song Name (Official Video)', search._norm('Song Name'))
    assert sim == 1.0


def test_title_sim_low_for_unrelated():
    sim, exact = search._title_sim('Completely Different', search._norm('Song Name'))
    assert sim < search._TITLE_GATE and exact is False


def test_artist_sim_substring_bonus():
    assert search._artist_sim('hikaruutada', 'utada') == 0.8
    assert search._artist_sim('utada', 'utada') == 1.0
    assert search._artist_sim('', 'utada') == 0.0


@pytest.mark.parametrize('title', [
    'Song (Cover)', 'Song KARAOKE', 'Song - instrumental', 'Song [Nightcore]',
    'Song カラオケ', '歌ってみた Song', 'Song (Off Vocal)', 'Song sped up',
])
def test_is_derivative_flags_known_markers(title):
    assert search._is_derivative(title) is True


def test_is_derivative_ignores_plain_title():
    assert search._is_derivative('Song Name') is False


def test_score_rejects_below_title_gate():
    item = {'title': 'Zzz Qqq Www', 'artists': [{'name': 'X'}], 'duration_seconds': 100}
    assert search._score(item, search._norm('Song Name'), search._norm('A'), 100) == float('-inf')


def test_score_prefers_matching_duration():
    base = {'title': 'Song', 'artists': [{'name': 'A'}], 'resultType': 'song'}
    close = search._score({**base, 'duration_seconds': 200}, search._norm('Song'), search._norm('A'), 200)
    far = search._score({**base, 'duration_seconds': 260}, search._norm('Song'), search._norm('A'), 200)
    assert close > far


def test_score_penalises_derivative():
    tn, an = search._norm('Song'), search._norm('A')
    orig = search._score({'title': 'Song', 'artists': [{'name': 'A'}], 'duration_seconds': 200}, tn, an, 200)
    cover = search._score({'title': 'Song (Cover)', 'artists': [{'name': 'A'}], 'duration_seconds': 200}, tn, an, 200)
    assert orig > cover


def test_best_skips_items_without_video_id():
    results = [
        {'title': 'Song', 'artists': [{'name': 'A'}], 'duration_seconds': 200},
        {'videoId': 'ok', 'title': 'Song', 'artists': [{'name': 'A'}], 'duration_seconds': 200},
    ]
    exact, same, any_best = search._best(results, search._norm('Song'), search._norm('A'), 200)
    assert exact[0]['videoId'] == 'ok' and exact[1] > 0
    assert same[0]['videoId'] == 'ok'
    assert any_best[0]['videoId'] == 'ok'


def test_best_on_empty():
    assert search._best([], 'x', 'y', 0) == (search._NOTHING,) * 3


def test_best_separates_derivative_from_original():
    """Инструментал совпадает по всем признакам идеально и по очкам выигрывает"""
    results = [
        {'videoId': 'inst', 'title': 'Song (Instrumental)', 'artists': [{'name': 'A'}],
         'duration_seconds': 200, 'resultType': 'song'},
        {'videoId': 'orig', 'title': 'Song - live upload', 'artists': [{'name': 'Reup'}],
         'duration_seconds': 220, 'resultType': 'video'},
    ]
    exact, _, any_best = search._best(results, search._norm('Song'), search._norm('A'), 200)
    assert any_best[0]['videoId'] == 'inst', 'по очкам всё ещё выигрывает инструментал'
    assert exact[0]['videoId'] == 'orig', 'но студийного вида — только оригинал'


def test_best_returns_matching_kind_of_derivative():
    """Просили инструментал — не подсовываем другую нестудийную версию"""
    results = [
        {'videoId': 'firsttake', 'title': 'Song - From THE FIRST TAKE',
         'artists': [{'name': 'A'}], 'duration_seconds': 200, 'resultType': 'song'},
        {'videoId': 'inst', 'title': 'Song -Instrumental-', 'artists': [{'name': 'A'}],
         'duration_seconds': 205, 'resultType': 'song'},
    ]
    want = search._deriv_markers('Song -Instrumental-')
    exact, same, _ = search._best(results, search._norm('Song'), search._norm('A'), 205,
                                  want_markers=want)
    assert exact[0]['videoId'] == 'inst'
    assert same[0]['videoId'] in {'inst', 'firsttake'}, 'обе версии нестудийные'


def test_deriv_markers_are_categories():
    m = search._deriv_markers
    assert m('Song (Instrumental)') == frozenset({'instrumental'})
    assert m('Song インスト') == frozenset({'instrumental'}), 'CJK сводится к той же категории'
    assert m('Song - From THE FIRST TAKE') == frozenset({'firsttake'})
    assert m('Song (Live)') == frozenset({'live'})
    assert m('Song Name') == frozenset()


class _Feed:
    """Подмена _search_call: отдаёт заготовленную выдачу и помнит, о чём спрашивали"""

    def __init__(self, songs, videos):
        self.data = {'songs': songs, 'videos': videos}
        self._lock = threading.Lock()
        self.queried = []
        self.queries = []

    def __call__(self, query, filt):
        # задания идут параллельно, поэтому список общий для потоков,
        # а порядок в нём проверять нельзя
        with self._lock:
            self.queried.append(filt)
            self.queries.append(query)
        return self.data.get(filt, [])


# оригиналы захватываем до любых подмен: фикстура ниже ставит на их место заглушки
_REAL_ARTIST_ID = search._artist_id
_REAL_ARTIST_ID_CALL = search._artist_id_call


@pytest.fixture
def real_artist_call(monkeypatch):
    """Вернуть настоящий _artist_id_call

    Автофикстура держит его заглушённым, чтобы _lookup не ходил в сеть. Но
    _artist_id вызывает именно его, поэтому тестам самого _artist_id нужен
    оригинал — сеть у них закрыта подменой ytm
    """
    monkeypatch.setattr(search, '_artist_id_call', _REAL_ARTIST_ID_CALL)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """Держим модуль офлайн

    _lookup спрашивает артиста отдельным заданием, мимо подменённого поиска,
    поэтому заглушить надо и его — иначе тест уйдёт в настоящий YT Music
    """
    search._lookup.cache_clear()
    search._artist_ids.clear()
    monkeypatch.setattr(search, '_artist_id', lambda name: None)
    monkeypatch.setattr(search, '_artist_id_call', lambda name: (_ for _ in ()).throw(search._Empty()))
    yield
    search._lookup.cache_clear()
    search._artist_ids.clear()


def test_lookup_always_checks_both_filters(monkeypatch):
    """Раннего выхода по songs больше нет: официальные клипы лежат в videos

    Он экономил один запрос, но систематически возвращал не ту запись —
    инструментал ClariS, затем версии From THE FIRST TAKE
    """
    songs = [{'videoId': 'good', 'title': 'Song', 'artists': [{'name': 'A'}],
              'duration_seconds': 200, 'resultType': 'song'}]
    feed = _Feed(songs, [])
    monkeypatch.setattr(search, '_search_call', feed)
    assert search._lookup('Song', 'A', 200) == 'good'
    assert sorted(feed.queried) == ['songs', 'videos']


def test_lookup_checks_videos_when_artist_mismatched(monkeypatch):
    songs = [{'videoId': 'coverlabel', 'title': 'Song', 'artists': [{'name': 'Geek Music'}],
              'duration_seconds': 200, 'resultType': 'song'}]
    videos = [{'videoId': 'original', 'title': 'Song', 'artists': [{'name': 'A'}],
               'duration_seconds': 200, 'resultType': 'video'}]
    feed = _Feed(songs, videos)
    monkeypatch.setattr(search, '_search_call', feed)
    assert search._lookup('Song', 'A', 200) == 'original'
    assert sorted(feed.queried) == ['songs', 'videos']


def test_lookup_falls_back_to_first_when_nothing_passes_gate(monkeypatch):
    songs = [{'videoId': 'whatever', 'title': 'Totally Unrelated', 'artists': [], 'duration_seconds': 10}]
    monkeypatch.setattr(search, '_search_call', _Feed(songs, []))
    assert search._lookup('Song Name', 'A', 200) == 'whatever'


def test_lookup_returns_none_on_empty(monkeypatch):
    monkeypatch.setattr(search, '_search_call', _Feed([], []))
    assert search._lookup('Song', 'A', 200) is None


def test_catalog_lookup_guards(monkeypatch):
    monkeypatch.setattr(search, 'ytm', None)
    assert search.ytm_catalog_lookup('Song') is None


def test_title_sim_reads_song_name_from_japanese_brackets():
    """Официальные японские заголовки кладут имя песни в 『』

    Вариант без скобок его терял, кандидат не проходил порог названия,
    и официальный клип ClariS отсекался в пользу инструментала
    """
    official = 'ClariS『コイセカイ』 Music Video 【TVアニメ「白聖女と黒牧師」オープニングテーマ】'
    sim, exact = search._title_sim(official, search._norm('コイセカイ'))
    assert exact is True and sim == 1.0


@pytest.mark.parametrize('title,target', [
    ('Artist『Song』 Music Video', 'Song'),
    ('Artist「Song」full ver.', 'Song'),
    ('Artist (Song) MV', 'Song'),
    ('Artist [Song] official', 'Song'),
])
def test_title_sim_finds_target_inside_any_brackets(title, target):
    sim, _ = search._title_sim(title, search._norm(target))
    assert sim == 1.0


def test_bracket_contents_only_add_candidates():
    """Правка аддитивная: то, что совпадало раньше, совпадает и сейчас"""
    sim, exact = search._title_sim('Song Name (Official Video)', search._norm('Song Name'))
    assert exact is True and sim == 1.0


# --- маркеры альтернативных версий ------------------------------------------

@pytest.mark.parametrize('title', [
    'GIRI GIRI - From THE FIRST TAKE (feat. Suu)',
    'Song - From THE FIRST TAKE',
    'Song (TV Size Ver.)',
    'Song (Live)',
    'Song [Live]',
    'Song (Live Version)',
    'コネクト (ClariS 1st 武道館コンサート) [Live]',
])
def test_alternative_versions_flagged(title):
    """Записи той же песни, которые студийный оригинал не заменяют"""
    assert search._is_derivative(title) is True


@pytest.mark.parametrize('title', [
    'Live Your Life',
    'Alive',
    'Song Name',
    'Living in the Moment',
])
def test_live_word_alone_is_not_a_marker(title):
    """'live' отдельным словом ловил бы обычные названия, метка только в скобках"""
    assert search._is_derivative(title) is False


# --- запрос альтернативной версии -------------------------------------------

@pytest.mark.parametrize('title,expected', [
    ('ヒトリゴト -Instrumental-', 'ヒトリゴト'),
    ('GIRI GIRI (Instrumental)', 'GIRI GIRI'),
    ('Song - From THE FIRST TAKE', 'Song'),
    ('Song (TV Size Ver.)', 'Song'),
    ('Обычное название', 'Обычное название'),
])
def test_strip_markers(title, expected):
    assert search._strip_markers(title) == expected


def test_lookup_adds_base_query_for_marked_request(monkeypatch):
    """Запрос с пометкой версии находит не всё, поэтому кандидатов добираем

    По «ClariS ヒトリゴト Instrumental-» инструментал первый в выдаче, но так
    везёт не всегда, и второй запрос по базовому названию оставляем
    """
    inst = {'videoId': 'inst', 'title': 'Song -Instrumental-', 'artists': [{'name': 'A'}],
            'duration_seconds': 200, 'resultType': 'song'}
    cover = {'videoId': 'cover', 'title': 'Song (Cover)', 'artists': [{'name': 'A'}],
             'duration_seconds': 200, 'resultType': 'song'}

    queries = []

    def _fake(query, filt):
        queries.append(query)
        # по запросу с пометкой инструментала нет, он находится только по базовому
        return [cover] if 'Instrumental' in query else [inst, cover]

    monkeypatch.setattr(search, '_search_call', _fake)
    assert search._lookup('Song -Instrumental-', 'A', 200) == 'inst'
    assert any('Instrumental' in q for q in queries)
    assert any(q == 'A Song' for q in queries), 'базовый запрос не сделан'


def test_lookup_does_not_add_base_query_for_plain_request(monkeypatch):
    """Лишний запрос нужен только когда в названии есть пометка версии"""
    feed = _Feed([{'videoId': 'ok', 'title': 'Song', 'artists': [{'name': 'A'}],
                   'duration_seconds': 200, 'resultType': 'song'}], [])
    monkeypatch.setattr(search, '_search_call', feed)
    search._lookup('Song', 'A', 200)
    assert sorted(feed.queried) == ['songs', 'videos'], 'ровно два запроса, без добора'


def test_merge_drops_duplicates_and_keeps_order():
    a = [{'videoId': '1', 'title': 'x'}, {'videoId': '2', 'title': 'y'}]
    b = [{'videoId': '2', 'title': 'y'}, {'videoId': '3', 'title': 'z'}]
    assert [i['videoId'] for i in search._merge(a, b)] == ['1', '2', '3']


# --- сопоставление артиста по ID --------------------------------------------

def _item(artists):
    return {'videoId': 'x', 'title': 'Song',
            'artists': [{'name': n, 'id': i} for n, i in artists],
            'duration_seconds': 200, 'resultType': 'song'}


def test_artist_match_by_id_across_scripts():
    """Spotify пишет 藤川千愛, YouTube — Chiai Fujikawa; строки не связываются"""
    item = _item([('Chiai Fujikawa', 'UC_chiai')])
    assert search._artist_sim(search._norm('Chiai Fujikawa'), search._norm('藤川千愛')) == 0.0
    assert search._artist_match(item, search._norm('藤川千愛'), 'UC_chiai') == 1.0


def test_artist_match_falls_back_to_string_without_id():
    item = _item([('ClariS', 'UC_claris')])
    assert search._artist_match(item, search._norm('ClariS'), None) == 1.0


def test_artist_match_rejects_foreign_id():
    """Чужой исполнитель не должен получить бонус за совпадение ID"""
    item = _item([('Tomoya Uzuki', 'UC_uzuki')])
    assert search._artist_match(item, search._norm('QUEEN BEE'), 'UC_queenbee') < 0.6


def test_artist_match_survives_items_without_ids():
    item = {'videoId': 'x', 'title': 'Song', 'artists': [{'name': 'ClariS'}]}
    assert search._artist_match(item, search._norm('ClariS'), 'UC_claris') == 1.0


def test_score_uses_artist_id(monkeypatch):
    """Тот же кандидат с ID своего артиста должен набирать больше"""
    item = _item([('Chiai Fujikawa', 'UC_chiai')])
    tn, an = search._norm('Song'), search._norm('藤川千愛')
    without = search._score(item, tn, an, 200)
    with_id = search._score(item, tn, an, 200, 'UC_chiai')
    assert with_id > without
    assert with_id - without == pytest.approx(25.0)


def test_lookup_passes_artist_id_into_scoring(monkeypatch):
    """Оркестровый кавер не должен обгонять своего исполнителя"""
    orgel = {'videoId': 'orgel', 'title': 'Borderland',
             'artists': [{'name': 'MIDORI ORGEL', 'id': 'UC_orgel'}],
             'duration_seconds': 200, 'resultType': 'song'}
    real = {'videoId': 'real', 'title': 'Borderland',
            'artists': [{'name': 'Mami Kawada', 'id': 'UC_mami'}],
            'duration_seconds': 203, 'resultType': 'song'}
    monkeypatch.setattr(search, '_search_call', _Feed([orgel, real], []))
    monkeypatch.setattr(search, '_artist_id_call', lambda name: 'UC_mami')
    assert search._lookup('Borderland', '川田まみ', 200) == 'real'


def test_artist_id_returns_none_without_ytm(monkeypatch, real_artist_call):
    monkeypatch.setattr(search, 'ytm', None)
    assert _REAL_ARTIST_ID('ClariS') is None


def test_artist_id_survives_search_error(monkeypatch, real_artist_call):
    class Boom:
        def search(self, *a, **k):
            raise RuntimeError('ytm упал')

    monkeypatch.setattr(search, 'ytm', Boom())
    assert _REAL_ARTIST_ID('ClariS') is None


def test_artist_id_skips_entries_without_browse_id(monkeypatch, real_artist_call):
    class Stub:
        def search(self, *a, **k):
            return [{'artist': 'X'}, {'browseId': 'UC_ok', 'artist': 'ClariS'}]

    monkeypatch.setattr(search, 'ytm', Stub())
    assert _REAL_ARTIST_ID('ClariS') == 'UC_ok'


def test_artist_id_retries_on_empty_result(monkeypatch, real_artist_call):
    """Пустая выдача приходит без ошибки: QUEEN BEE резолвился 1 раз из 5"""
    class Flaky:
        def __init__(self):
            self.calls = 0

        def search(self, *a, **k):
            self.calls += 1
            return [] if self.calls < 2 else [{'browseId': 'UC_qb', 'artist': 'Queen Bee'}]

    stub = Flaky()
    monkeypatch.setattr(search, 'ytm', stub)
    assert _REAL_ARTIST_ID('QUEEN BEE') == 'UC_qb'
    assert stub.calls == 2


def test_artist_id_retries_on_exception(monkeypatch, real_artist_call):
    """ytm.search изредка отдаёт не-JSON и падает с JSONDecodeError"""
    class Flaky:
        def __init__(self):
            self.calls = 0

        def search(self, *a, **k):
            self.calls += 1
            if self.calls == 1:
                raise ValueError('Expecting value: line 1 column 1')
            return [{'browseId': 'UC_ok'}]

    stub = Flaky()
    monkeypatch.setattr(search, 'ytm', stub)
    assert _REAL_ARTIST_ID('X') == 'UC_ok'
    assert stub.calls == 2


def test_artist_id_gives_up_within_budget(monkeypatch, real_artist_call):
    """Несуществующий артист не должен молотить запросы бесконечно"""
    class Always:
        def __init__(self):
            self.calls = 0

        def search(self, *a, **k):
            self.calls += 1
            return []

    stub = Always()
    monkeypatch.setattr(search, 'ytm', stub)
    monkeypatch.setattr(search, '_ARTIST_LOOKUP_BUDGET', 0.4)
    monkeypatch.setattr(search, '_ARTIST_RETRY_PAUSE', 0.05)
    assert _REAL_ARTIST_ID('нет такого') is None
    assert 1 <= stub.calls <= 10, f'цикл не должен крутиться вхолостую, попыток {stub.calls}'


def test_artist_id_stops_when_budget_exhausted(monkeypatch, real_artist_call):
    """Медленный ответ не должен задерживать воспроизведение вторым заходом"""
    class Slow:
        def __init__(self):
            self.calls = 0

        def search(self, *a, **k):
            self.calls += 1
            monkeypatch.setattr(search.time, 'monotonic', lambda: 10_000.0)
            return []

    stub = Slow()
    monkeypatch.setattr(search.time, 'monotonic', lambda: 0.0)
    monkeypatch.setattr(search, 'ytm', stub)
    assert _REAL_ARTIST_ID('X') is None
    assert stub.calls == 1, 'после исчерпания бюджета повтора быть не должно'


def test_artist_id_caches_only_success(monkeypatch, real_artist_call):
    """Неудача часто временная: следующий трек обязан попробовать снова"""
    class Once:
        def __init__(self):
            self.calls = 0

        def search(self, *a, **k):
            self.calls += 1
            return [{'browseId': 'UC_late'}] if self.calls > 2 else []

    stub = Once()
    monkeypatch.setattr(search, 'ytm', stub)
    monkeypatch.setattr(search, '_ARTIST_LOOKUP_BUDGET', 0.0)
    assert _REAL_ARTIST_ID('X') is None
    assert 'X' not in search._artist_ids, 'неудачу кэшировать нельзя'
    assert _REAL_ARTIST_ID('X') is None
    monkeypatch.setattr(search, '_ARTIST_LOOKUP_BUDGET', 1.0)
    monkeypatch.setattr(search, '_ARTIST_RETRY_PAUSE', 0.01)
    assert _REAL_ARTIST_ID('X') == 'UC_late'
    assert search._artist_ids['X'] == 'UC_late', 'удачу кэшируем'
    stub.calls = 0
    assert _REAL_ARTIST_ID('X') == 'UC_late'
    assert stub.calls == 0, 'повторный запрос из кэша не ходит в сеть'


@pytest.mark.parametrize('title,expected', [
    ('SWEET HURT（ReoNa ONE-MAN Concert 2023「ピルグリム」）', {'live'}),
    ('WORLD END (FLOW 超会議 2020 LIVE at 幕張メッセイベントホール)', {'live'}),
    ('ONE-MAN LIVE TOUR 2024', {'live'}),
    # ловушки: голое 'concert' пометило бы классику, 'live' отдельным словом —
    # обычные названия
    ('Piano Concerto No. 1 in B-flat minor', set()),
    ('Violin Concerto', set()),
    ('Live Your Life', set()),
    ('SWEET HURT', set()),
])
def test_концертные_записи_не_проходят_за_оригинал(title, expected):
    """ReoNa "SWEET HURT" уходил на запись с концерта: из живых пометок были
    только японские コンサート и 武道館"""
    assert search._deriv_markers(title) == frozenset(expected)


def test_живая_запись_проигрывает_студийной(monkeypatch):
    """Бонус за имя артиста в заголовке (+8, для аниме-OP и реаплоадов) давал
    концертной записи перевес над чистым оригиналом"""
    studio = {'videoId': 'studio', 'title': 'SWEET HURT', 'duration_seconds': 278,
              'artists': [{'name': 'ReoNa', 'id': 'UC_reona'}], 'resultType': 'song'}
    live = {'videoId': 'live', 'title': 'SWEET HURT（ReoNa ONE-MAN Concert 2023）',
            'duration_seconds': 275,
            'artists': [{'name': 'ReoNa', 'id': 'UC_reona'}], 'resultType': 'song'}
    monkeypatch.setattr(search, '_search_call', _Feed([live, studio], []))
    monkeypatch.setattr(search, '_artist_id_call', lambda name: 'UC_reona')

    assert search._lookup('SWEET HURT', 'ReoNa', 277) == 'studio'


class _Artists:
    """Подмена ytm для поиска артистов: отдаёт заготовленный список"""

    def __init__(self, entries):
        self.entries = entries

    def search(self, query, filter=None, limit=None):
        return list(self.entries)


def test_артист_выбирается_по_точному_имени(monkeypatch, real_artist_call):
    """YouTube на короткое имя первым отдаёт чужого: по "FLOW" — бразильского
    Flow Firme, чей каталог не содержит нужного трека, а настоящий FLOW идёт
    вторым"""
    monkeypatch.setattr(search, 'ytm', _Artists([
        {'artist': 'Flow Firme', 'browseId': 'UC_wrong'},
        {'artist': 'FLOW', 'browseId': 'UC_right'},
    ]))
    assert _REAL_ARTIST_ID('FLOW') == 'UC_right'


def test_без_точного_совпадения_берём_первого(monkeypatch, real_artist_call):
    """Ромадзи с кандзи сравнивать бесполезно: 藤川千愛 против Chiai Fujikawa
    даёт 0.0, а это верная пара"""
    monkeypatch.setattr(search, 'ytm', _Artists([
        {'artist': 'Chiai Fujikawa', 'browseId': 'UC_chiai'},
        {'artist': 'kobasolo', 'browseId': 'UC_other'},
    ]))
    assert _REAL_ARTIST_ID('藤川千愛') == 'UC_chiai'


def test_точное_совпадение_ищется_без_учёта_регистра(monkeypatch, real_artist_call):
    monkeypatch.setattr(search, 'ytm', _Artists([
        {'artist': 'Ray Charles', 'browseId': 'UC_charles'},
        {'artist': 'RAY', 'browseId': 'UC_ray'},
    ]))
    assert _REAL_ARTIST_ID('Ray') == 'UC_ray'


@pytest.mark.parametrize('title,expected', [
    ('楽園PROJECT／ Ray【coverby叶羽】', {'cover'}),
    ('楽園PROJECT (Originally Performed by Ray)', {'karaoke'}),
    ('楽園PROJECT', set()),
    # ловушка: 'coverby' не должен ловить обычные слова
    ('Discover', set()),
])
def test_слитные_пометки_каверов(title, expected):
    """Пробелов в 【coverby叶羽】 нет, на токены оно не разбирается"""
    assert search._deriv_markers(title) == frozenset(expected)


@pytest.mark.parametrize('cand,target', [
    # YT Music дописывает романизацию через « - », а разделитель ・ стоит ВНУТРИ
    # катаканы: общий разбор рассыпал название на «エクストラ», «マジック», «アワー»
    ('エクストラ・マジック・アワー - Extra Magic Hour', 'エクストラ・マジック・アワー'),
    ('メフィスト - Mephisto', 'メフィスト'),
    ('スイートメモリー - Sweet Memory', 'Sweet Memory'),
    ('楽園PROJECT - Rakuen Project', '楽園PROJECT'),
])
def test_романизация_через_дефис_не_рушит_сходство(cand, target):
    sim, exact = search._title_sim(cand, search._norm(target))
    assert (sim, exact) == (1.0, True), f'{cand!r} против {target!r}'


def test_официальная_запись_обгоняет_версию_с_подзаголовком(monkeypatch):
    """AKINO with bless4 уходил на запись bless4 с пометкой OPテーマ曲: у верной
    записи YT Music дописал романизацию, и сходство названия падало до 0.63"""
    right = {'videoId': 'right', 'title': 'エクストラ・マジック・アワー - Extra Magic Hour',
             'duration_seconds': 256, 'artists': [{'name': 'AKINO with bless4'}],
             'resultType': 'song'}
    wrong = {'videoId': 'wrong', 'title': 'エクストラ・マジック・アワー（甘城ブリリアントパーク OPテーマ曲）',
             'duration_seconds': 259, 'artists': [{'name': 'bless4'}], 'resultType': 'song'}
    monkeypatch.setattr(search, '_search_call', _Feed([wrong, right], []))

    assert search._lookup('エクストラ・マジック・アワー', 'AKINO with bless4', 255) == 'right'


def test_неудачный_подбор_не_кэшируется(monkeypatch):
    """Закэшированный None выключал бы трек до перезапуска, а None получается
    ровно тогда, когда YouTube заблокировал запросы"""
    search._lookup.cache_clear()
    monkeypatch.setattr(search, '_search_call', _Feed([], []))
    assert search._lookup('Song', 'A', 200) is None

    good = {'videoId': 'ok', 'title': 'Song', 'artists': [{'name': 'A'}],
            'duration_seconds': 200, 'resultType': 'song'}
    monkeypatch.setattr(search, '_search_call', _Feed([good], []))
    assert search._lookup('Song', 'A', 200) == 'ok', 'неудача осела в кэше'


def test_удачный_подбор_кэшируется(monkeypatch):
    """Иначе каждый повтор трека стоил бы новых запросов к YT Music"""
    search._lookup.cache_clear()
    good = {'videoId': 'ok', 'title': 'Song', 'artists': [{'name': 'A'}],
            'duration_seconds': 200, 'resultType': 'song'}
    feed = _Feed([good], [])
    monkeypatch.setattr(search, '_search_call', feed)

    assert search._lookup('Song', 'A', 200) == 'ok'
    assert search._lookup('Song', 'A', 200) == 'ok'
    assert sorted(feed.queried) == ['songs', 'videos'], 'второй раз пошёл в сеть'


def test_кэш_подбора_ограничен(monkeypatch):
    """Бот работает сутками, безразмерный словарь течёт"""
    search._lookup.cache_clear()

    def feed(query, filt):
        return [{'videoId': f'v{query}', 'title': query.split()[-1],
                 'artists': [{'name': 'A'}], 'duration_seconds': 200,
                 'resultType': 'song'}] if filt == 'songs' else []

    monkeypatch.setattr(search, '_search_call', feed)
    for i in range(search._LOOKUP_CACHE_MAX + 20):
        search._lookup(f'T{i}', 'A', 200)
    assert len(search._lookup_cache) <= search._LOOKUP_CACHE_MAX


@pytest.mark.parametrize('title,expected', [
    ('Stray Kids 「TOP -Japanese ver.-」 Fan Featuring Guide Video', {'guide'}),
    ('Stray Kids 『TOP -Japanese ver.-』Special Performance Movie', {'performance'}),
    ('Artist - Song (Dance Practice)', {'dance'}),
    ('Making of the Album', {'behind'}),
    # соседние слова не должны цеплять
    ('Stray Kids 『TOP -Japanese ver.-』Music Video', set()),
    ('Guide Me Home', set()),
    ('Concerto in D minor', set()),
])
def test_неформатные_ролики_канала_помечаются(title, expected):
    """Официальный канал выкладывает не только песню

    У guide- и performance-роликов заголовок, исполнитель и длительность
    совпадают с оригиналом идеально, поэтому очками они не отсеиваются
    """
    assert search._deriv_markers(title) == frozenset(expected)


def test_guide_видео_проигрывает_клипу(monkeypatch):
    """Регрессия: guide-видео обходило клип за счёт более точной длительности"""
    guide = {'videoId': 'guide',
             'title': 'Stray Kids 「TOP -Japanese ver.-」 Fan Featuring Guide Video',
             'duration_seconds': 189,
             'artists': [{'name': 'Stray Kids', 'id': 'UC_skz'}], 'resultType': 'video'}
    mv = {'videoId': 'mv', 'title': 'Stray Kids 『TOP -Japanese ver.-』Music Video',
          'duration_seconds': 191,
          'artists': [{'name': 'Stray Kids', 'id': 'UC_skz'}], 'resultType': 'video'}
    monkeypatch.setattr(search, '_search_call', _Feed([], [guide, mv]))
    monkeypatch.setattr(search, '_artist_id_call', lambda name: 'UC_skz')
    search._lookup.cache_clear()

    got = search.ytm_catalog_lookup('TOP -Japanese ver.-', 'Stray Kids', 188.8)
    assert got == 'mv', 'должна выиграть настоящая запись песни'


def test_единственный_неформатный_ролик_всё_же_берётся(monkeypatch):
    """Пометка опускает ролик ниже песни, но не выкидывает его

    Иначе трек, у которого на YT есть только guide-видео, молча пропадёт
    """
    guide = {'videoId': 'guide', 'title': 'Song Fan Featuring Guide Video',
             'duration_seconds': 200,
             'artists': [{'name': 'A', 'id': 'UC_a'}], 'resultType': 'video'}
    monkeypatch.setattr(search, '_search_call', _Feed([], [guide]))
    monkeypatch.setattr(search, '_artist_id_call', lambda name: 'UC_a')
    search._lookup.cache_clear()

    assert search.ytm_catalog_lookup('Song', 'A', 200) == 'guide'


@pytest.mark.parametrize('parts,expected', [
    # ведущий дефис — оператор исключения, слово надо сохранить
    (('Mili', 'Entertainment -Goblin Slayer II Opening Theme'),
     'Mili Entertainment Goblin Slayer II Opening Theme'),
    (('ClariS', 'ヒトリゴト -Instrumental-'), 'ClariS ヒトリゴト Instrumental-'),
    # дефис внутри слова и с пробелами оператором не является
    (('K-On!', 'Don\'t say "lazy"'), 'K-On! Don\'t say "lazy"'),
    (('A', 'Song - Remix'), 'A Song - Remix'),
    (('', 'Song'), 'Song'),
])
def test_query_drops_exclusion_operator(parts, expected):
    assert search._query(*parts) == expected


def test_lookup_query_carries_no_operator(monkeypatch):
    """Запрос уходит уже без оператора, иначе выдача теряет слово названия"""
    feed = _Feed([{'videoId': 'ok', 'title': 'Song -Sub Title', 'artists': [{'name': 'A'}],
                   'duration_seconds': 200, 'resultType': 'song'}], [])
    monkeypatch.setattr(search, '_search_call', feed)
    search._lookup('Song -Sub Title', 'A', 200)
    assert all('-Sub' not in q for q in feed.queries)
