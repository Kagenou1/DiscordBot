"""Локальный сервер выдачи PO Token

С токеном YouTube отдаёт opus по ссылкам, валидным сразу. Сервер нужен именно
как процесс: дорогая часть (чеканщик BotGuard) живёт в его памяти, а
одноразовый скрипт пересоздаёт её на каждый трек и начинает отказывать
"""
import subprocess

import pytest

from audio.youtube import pot


@pytest.fixture(autouse=True)
def _no_stray_process():
    """Тесты не должны оставлять живых процессов"""
    yield
    pot._proc = None


def test_недоступен_без_скрипта(monkeypatch, tmp_path):
    monkeypatch.setattr(pot, '_SERVER_JS', tmp_path / 'нет.js')
    assert pot.available() is False


def test_недоступен_без_node(monkeypatch, tmp_path):
    js = tmp_path / 'main.js'
    js.write_text('', encoding='utf-8')
    monkeypatch.setattr(pot, '_SERVER_JS', js)
    monkeypatch.setattr(pot, '_LOCAL_NODE', tmp_path / 'нет-node')
    monkeypatch.setattr('shutil.which', lambda name: None)
    assert pot.available() is False


def test_старт_без_провайдера_не_падает(monkeypatch, tmp_path):
    """Бот обязан работать и без сервера, просто медленнее"""
    monkeypatch.setattr(pot, '_SERVER_JS', tmp_path / 'нет.js')
    monkeypatch.setattr(pot, '_port_busy', lambda port=pot.PORT: False)
    assert pot.start() is False


def test_чужой_сервер_переиспользуется(monkeypatch):
    """Порт занят — значит кто-то уже выдаёт токены, второй процесс не нужен"""
    monkeypatch.setattr(pot, '_port_busy', lambda port=pot.PORT: True)
    spawned = []
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: spawned.append(a))

    assert pot.start() is True
    assert not spawned, 'подняли второй сервер поверх живого'


def test_сбой_запуска_не_роняет_бота(monkeypatch, tmp_path):
    js = tmp_path / 'main.js'
    js.write_text('', encoding='utf-8')
    monkeypatch.setattr(pot, '_SERVER_JS', js)
    monkeypatch.setattr(pot, '_LOCAL_NODE', tmp_path / 'node.exe')
    monkeypatch.setattr(pot, '_node', lambda: 'node')
    monkeypatch.setattr(pot, '_port_busy', lambda port=pot.PORT: False)

    def boom(*a, **k):
        raise OSError('нет такого файла')

    monkeypatch.setattr(subprocess, 'Popen', boom)
    assert pot.start() is False


class _Proc:
    def __init__(self):
        self.pid = 1234
        self.terminated = self.killed = 0

    def terminate(self):
        self.terminated += 1

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed += 1


def test_stop_гасит_процесс(monkeypatch):
    proc = _Proc()
    monkeypatch.setattr(pot, '_proc', proc)
    pot.stop()
    assert proc.terminated == 1
    assert pot._proc is None


def test_stop_без_процесса_безвреден(monkeypatch):
    monkeypatch.setattr(pot, '_proc', None)
    pot.stop()


def test_ready_ждёт_порт(monkeypatch):
    """Первый токен строит чеканщика, до этого сервер не отвечает"""
    calls = []

    def busy(port=pot.PORT):
        calls.append(1)
        return len(calls) >= 3

    monkeypatch.setattr(pot, '_port_busy', busy)
    assert pot.ready(timeout=2) is True
    assert len(calls) >= 3


def test_ready_сдаётся_по_таймауту(monkeypatch):
    monkeypatch.setattr(pot, '_port_busy', lambda port=pot.PORT: False)
    import time
    t0 = time.perf_counter()
    assert pot.ready(timeout=0.3) is False
    assert time.perf_counter() - t0 < 1.5
