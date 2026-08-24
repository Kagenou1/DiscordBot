"""Параллельный сбор запросов к YT Music с дублирующим запросом

Заблокированный запрос молчит 6-8 секунд: пограничный слой Google держит
соединение, прежде чем отдать страницу "Sorry...". Успешный укладывается
в 400 мс. На этом разрыве и строится дубль — молчание дольше секунды почти
наверняка блокировка
"""
import time

import pytest

from audio.youtube import search


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    """Ускоряем пороги, иначе тесты ждали бы секундами"""
    monkeypatch.setattr(search, '_HEDGE_DELAY', 0.15)
    monkeypatch.setattr(search, '_GATHER_BUDGET', 3.0)


def test_быстрый_путь_обходится_одним_запросом():
    """Дубль не должен удваивать обычную нагрузку"""
    calls = []

    def fast():
        calls.append(1)
        return 'ok'

    assert search._gather({'a': fast}) == {'a': 'ok'}
    assert len(calls) == 1, 'на быстром пути дубль не нужен'


def test_дубль_обгоняет_молчащий_запрос():
    calls = []

    def blocked_then_fine():
        n = len(calls)
        calls.append(n)
        if n == 0:
            time.sleep(1.5)      # имитация блокировки
            return 'поздно'
        return 'вовремя'

    t0 = time.perf_counter()
    got = search._gather({'a': blocked_then_fine})
    dt = time.perf_counter() - t0

    assert got == {'a': 'вовремя'}
    assert dt < 1.0, f'ждали блокировку вместо дубля: {dt:.2f}с'


def test_сбой_повторяется_сразу():
    """Ошибка приходит быстро, ждать порога дубля незачем"""
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('403 Sorry')
        return 'ok'

    t0 = time.perf_counter()
    assert search._gather({'a': flaky})['a'] == 'ok'
    assert len(calls) == 2
    assert time.perf_counter() - t0 < 0.15, 'повтор после сбоя не должен ждать порога'


def test_попытки_ограничены():
    """Иначе постоянный сбой крутился бы вечно"""
    calls = []

    def always_fails():
        calls.append(1)
        raise RuntimeError('нет')

    got = search._gather({'a': always_fails})
    assert 'a' not in got
    assert len(calls) == search._HEDGE_ATTEMPTS


def test_потолок_времени_не_даёт_повиснуть(monkeypatch):
    """Повтор без потолка однажды уже дал 16 секунд до звука"""
    monkeypatch.setattr(search, '_GATHER_BUDGET', 0.4)

    def hangs():
        time.sleep(5)
        return 'поздно'

    t0 = time.perf_counter()
    got = search._gather({'a': hangs})
    dt = time.perf_counter() - t0

    assert got == {}, 'неполная выдача лучше зависшего подбора'
    assert dt < 2.0, f'потолок не сработал: {dt:.2f}с'


def test_задания_не_складывают_задержки(monkeypatch):
    """Три запроса по 0.3с последовательно дали бы 0.9с"""
    monkeypatch.setattr(search, '_HEDGE_DELAY', 5)   # дубль не должен влиять на замер

    def slow(tag):
        def fn():
            time.sleep(0.3)
            return tag
        return fn

    t0 = time.perf_counter()
    got = search._gather({'a': slow('a'), 'b': slow('b'), 'c': slow('c')})
    dt = time.perf_counter() - t0

    assert got == {'a': 'a', 'b': 'b', 'c': 'c'}
    assert dt < 0.65, f'задания шли по очереди: {dt:.2f}с'


def test_одно_молчащее_задание_не_держит_остальные():
    """Ради этого всё и делалось: блокировка одного не должна тормозить подбор"""
    def quick(tag):
        return lambda: tag

    calls = []

    def blocked():
        n = len(calls)
        calls.append(n)
        if n == 0:
            time.sleep(1.5)
            return 'поздно'
        return 'вовремя'

    t0 = time.perf_counter()
    got = search._gather({'songs': quick('s'), 'videos': quick('v'), 'artist': blocked})
    dt = time.perf_counter() - t0

    assert got == {'songs': 's', 'videos': 'v', 'artist': 'вовремя'}
    assert dt < 1.0, f'ждали заблокированное задание: {dt:.2f}с'


def test_пустой_список_заданий():
    assert search._gather({}) == {}


def test_дубль_не_летит_на_задание_из_очереди(monkeypatch):
    """Задание, стоящее в очереди пула, молчит не из-за сервера. Дубль тут
    только углубил бы очередь — так тесный пул сам себя добивал бы"""
    import concurrent.futures as cf

    tiny = cf.ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(search, '_POOL', tiny)
    monkeypatch.setattr(search, '_HEDGE_DELAY', 0.05)
    calls = []

    def slow(tag):
        def fn():
            calls.append(tag)
            time.sleep(0.3)
            return tag
        return fn

    try:
        jobs = {k: slow(k) for k in ('a', 'b', 'c', 'd')}
        got = search._gather(jobs)
        assert got == {k: k for k in jobs}
        # один поток: работает 'a', остальные стоят в очереди. Дублировать
        # можно только 'a' — при трёх попытках это максимум 4+2 запуска
        assert len(calls) <= len(jobs) + search._HEDGE_ATTEMPTS, f'лишние запуски: {calls}'
        for tag in ('b', 'c', 'd'):
            assert calls.count(tag) == 1, f'задание из очереди дублировали: {calls}'
    finally:
        tiny.shutdown(wait=False)


def test_пул_шире_одного_подбора():
    """Один подбор занимает до пяти заданий, гильдий может играть несколько"""
    assert search._POOL._max_workers >= 16
