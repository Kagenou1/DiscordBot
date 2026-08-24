"""Логирование: интерфейс _Tee, чистка старых файлов, сессии гильдий"""
import io
import os
import sys
import time

import pytest

import logs
from conftest import ROOT


def test_tee_writes_to_all_streams():
    a, b = io.StringIO(), io.StringIO()
    tee = logs._Tee(a, b)
    assert tee.write('hello') == 5
    assert a.getvalue() == b.getvalue() == 'hello'


def test_tee_survives_broken_stream():
    class Broken:
        def write(self, _):
            raise OSError('closed')

        def flush(self):
            raise OSError('closed')

    good = io.StringIO()
    tee = logs._Tee(Broken(), good)
    tee.write('x')
    tee.flush()
    assert good.getvalue() == 'x'


def test_tee_looks_like_a_file_object():
    """yt-dlp и subprocess ждут от подменённого stdout полный файловый интерфейс"""
    tee = logs._Tee(sys.__stdout__, io.StringIO())
    assert isinstance(tee.isatty(), bool)
    assert isinstance(tee.encoding, str) and tee.encoding
    assert isinstance(tee.errors, str)
    assert tee.writable() is True
    assert tee.readable() is False
    assert tee.closed is False
    tee.writelines(['a', 'b'])


def test_tee_fileno_raises_without_descriptor():
    tee = logs._Tee(io.StringIO())
    with pytest.raises(OSError):
        tee.fileno()


def test_cleanup_removes_only_old_files(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, 'LOG_DIR', tmp_path)
    old = tmp_path / 'session-old.log'
    fresh = tmp_path / 'session-new.log'
    old.write_text('x', encoding='utf-8')
    fresh.write_text('x', encoding='utf-8')
    stale = time.time() - logs.RETENTION_SECONDS - 60
    os.utime(old, (stale, stale))

    assert logs._cleanup_old_logs() == 1
    assert not old.exists() and fresh.exists()


def test_cleanup_keeps_open_guild_file(tmp_path, monkeypatch):
    """Открытую сессию гильдии нельзя удалять, даже если в неё сутки не писали"""
    monkeypatch.setattr(logs, 'LOG_DIR', tmp_path)
    monkeypatch.setattr(logs, '_guild_files', {})
    logs.open_guild_session(5, 'Гильдия')
    path = logs._guild_files[5].path
    stale = time.time() - logs.RETENTION_SECONDS - 60
    os.utime(path, (stale, stale))

    assert logs._cleanup_old_logs() == 0
    assert path.exists()
    logs.close_guild_session(5)


def test_guild_session_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, 'LOG_DIR', tmp_path)
    monkeypatch.setattr(logs, '_guild_files', {})

    logs.session_log(1, 'до открытия')  # no-op, файла ещё нет
    logs.open_guild_session(1, 'Пещера/бомжей')
    logs.open_guild_session(1, 'повтор')  # no-op
    assert len(logs._guild_files) == 1

    path = logs._guild_files[1].path
    assert '/' not in path.name and 'Пещера_бомжей' in path.name

    logs.session_log(1, 'playing: X')
    logs.close_guild_session(1)

    text = path.read_text(encoding='utf-8')
    assert 'playing: X' in text and 'session closed' in text
    assert logs._guild_files == {}


def test_close_unknown_guild_is_noop(monkeypatch):
    monkeypatch.setattr(logs, '_guild_files', {})
    logs.close_guild_session(12345)


def test_force_utf8_console_survives_missing_streams(monkeypatch):
    monkeypatch.setattr(sys, '__stdout__', None)
    monkeypatch.setattr(sys, '__stderr__', object())  # без reconfigure
    logs._force_utf8_console()


def test_force_utf8_console_swallows_reconfigure_errors(monkeypatch):
    class Stubborn:
        encoding = 'cp1251'

        def reconfigure(self, **kwargs):
            raise OSError('поток не переключается')

    monkeypatch.setattr(sys, '__stdout__', Stubborn())
    monkeypatch.setattr(sys, '__stderr__', Stubborn())
    logs._force_utf8_console()


@pytest.mark.integration
def test_cjk_survives_piped_stdout(tmp_path):
    """Под пайпом Python берёт кодировку локали, на cp1251 такая строка терялась"""
    import subprocess

    script = (
        'import sys, pathlib\n'
        f'sys.path.insert(0, {str(ROOT)!r})\n'
        'import logs\n'
        f'logs.LOG_DIR = pathlib.Path({str(tmp_path)!r})\n'
        'logs.setup_logging()\n'
        'print("\u6b8b\u9177\u306a\u5929\u4f7f\u306e\u30c6\u30fc\u30bc")\n'
        'print("\u041a\u0438\u0440\u0438\u043b\u043b\u0438\u0446\u0430")\n'
    )
    env = {k: v for k, v in os.environ.items() if k != 'PYTHONIOENCODING'}
    proc = subprocess.run([sys.executable, '-c', script], capture_output=True, env=env)

    assert proc.returncode == 0, proc.stderr.decode('utf-8', 'replace')
    out = proc.stdout.decode('utf-8')
    assert '残酷な天使のテーゼ' in out
    assert 'Кириллица' in out

    written = next(tmp_path.glob('session-*.log')).read_text(encoding='utf-8')
    assert '残酷な天使のテーゼ' in written
