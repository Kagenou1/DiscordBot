"""Отпечаток набора слэш-команд

Глобальный sync жёстко лимитируется Discord, поэтому он дёргается только когда
отпечаток изменился. Значит отпечаток обязан меняться от всего, что видит
пользователь: иначе правка уедет в код, но не в Discord
"""
import sys

import pytest


@pytest.fixture
def fingerprint(monkeypatch):
    """client.py настраивает логирование прямо на импорте и перенаправляет
    stdout — в тестах это делать нельзя"""
    import logs
    monkeypatch.setattr(logs, 'setup_logging', lambda: None)
    monkeypatch.delitem(sys.modules, 'client', raising=False)
    import client
    monkeypatch.delitem(sys.modules, 'client', raising=False)
    return client._commands_fingerprint


class _Choice:
    def __init__(self, name, value):
        self.name, self.value = name, value


class _Param:
    def __init__(self, name, description='', required=False, choices=()):
        self.name = name
        self.description = description
        self.required = required
        self.choices = choices


class _Cmd:
    def __init__(self, name, description, parameters=()):
        self.name = name
        self.description = description
        self.parameters = parameters


class _Tree:
    def __init__(self, commands):
        self._commands = commands

    def get_commands(self):
        return self._commands


def _tree(**kw):
    cmd_desc = kw.pop('cmd_desc', 'играть')
    name = kw.pop('name', 'query')
    return _Tree([_Cmd('play', cmd_desc, [_Param(name, **kw)])])


def test_одинаковые_наборы_дают_один_отпечаток(fingerprint):
    assert fingerprint(_tree()) == fingerprint(_tree())


def test_порядок_команд_не_влияет(fingerprint):
    a = _Tree([_Cmd('play', 'a'), _Cmd('skip', 'b')])
    b = _Tree([_Cmd('skip', 'b'), _Cmd('play', 'a')])
    assert fingerprint(a) == fingerprint(b)


@pytest.mark.parametrize('changed,label', [
    ({'cmd_desc': 'другое'}, 'описание команды'),
    ({'description': 'другое'}, 'описание параметра'),
    ({'required': True}, 'обязательность параметра'),
    ({'choices': (_Choice('быстро', '1'),)}, 'список choices'),
    ({'name': 'другое'}, 'имя параметра'),
])
def test_видимая_правка_меняет_отпечаток(fingerprint, changed, label):
    """Всё это Discord показывает пользователю; без синхронизации он будет
    видеть старое"""
    assert fingerprint(_tree()) != fingerprint(_tree(**changed)), label


def test_правка_choices_различима_по_значению(fingerprint):
    a = _tree(choices=(_Choice('быстро', '1'),))
    b = _tree(choices=(_Choice('быстро', '2'),))
    assert fingerprint(a) != fingerprint(b)


def test_отпечаток_переживает_команду_без_параметров(fingerprint):
    """get_commands отдаёт и группы, у которых parameters нет вовсе"""
    class Bare:
        name, description = 'ping', 'проверка'

    assert fingerprint(_Tree([Bare()]))
