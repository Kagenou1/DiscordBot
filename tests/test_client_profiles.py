"""Переключение профилей player_client при отказе YouTube

YouTube периодически перестаёт обслуживать конкретного клиента: так случилось
с android_vr, который до этого работал долго. Бот не должен зависеть от одного
"""
import pytest

from audio.youtube import client as C


@pytest.fixture(autouse=True)
def _reset_profile():
    """Профиль — глобальное состояние процесса, изолируем тесты друг от друга"""
    active, last = C._active, C._last_rotate
    yield
    C._active, C._last_rotate = active, last
    C._apply_profile(C._active)


def _clients():
    return C.ytdl.params['extractor_args']['youtube']['player_client']


def test_profiles_are_well_formed():
    assert len(C._CLIENT_PROFILES) >= 2, 'один профиль не спасает от отказа клиента'
    names = [name for name, _ in C._CLIENT_PROFILES]
    assert len(names) == len(set(names)), 'имена профилей должны быть различимы в логе'
    for name, clients in C._CLIENT_PROFILES:
        assert name and clients, f'пустой профиль {name!r}'
        assert all(isinstance(c, str) for c in clients)


def test_профили_не_тянут_мёртвый_клиент():
    """android_vr отдаёт ссылки, отвечающие 403 всегда — клиент мёртв,
    а 'default' подмешивает именно его"""
    for name, clients in C._CLIENT_PROFILES:
        assert 'default' not in clients, f"{name}: 'default' подмешивает android_vr"
        assert 'android_vr' not in clients, f'{name}: android_vr не играет'


def test_apply_profile_rewrites_extractor_args():
    C._apply_profile(0)
    assert _clients() == list(C._CLIENT_PROFILES[0][1])
    C._apply_profile(1)
    assert _clients() == list(C._CLIENT_PROFILES[1][1])


def test_rotate_moves_to_next_profile():
    C._active, C._last_rotate = 0, 0.0
    C._apply_profile(0)

    name = C.rotate_profile()

    assert name == C._CLIENT_PROFILES[1][0]
    assert C.active_profile() == name
    assert _clients() == list(C._CLIENT_PROFILES[1][1])


def test_rotate_wraps_around():
    """Обойдя все профили, возвращаемся к первому, а не упираемся в конец"""
    C._active, C._last_rotate = 0, 0.0
    seen = [C.active_profile()]
    for _ in range(len(C._CLIENT_PROFILES)):
        C._last_rotate = 0.0
        seen.append(C.rotate_profile())
    assert seen[-1] == seen[0]
    assert set(seen) == {n for n, _ in C._CLIENT_PROFILES}


def test_rotate_respects_cooldown(monkeypatch):
    """Одно битое видео не должно прокрутить все профили подряд"""
    C._active, C._last_rotate = 0, 0.0
    assert C.rotate_profile() is not None
    before = C.active_profile()
    assert C.rotate_profile() is None, 'повтор сразу должен быть отклонён'
    assert C.active_profile() == before


def test_rotate_allowed_after_cooldown(monkeypatch):
    C._active, C._last_rotate = 0, 0.0
    C.rotate_profile()
    monkeypatch.setattr(C.time, 'monotonic', lambda: C._last_rotate + C._ROTATE_COOLDOWN + 1)
    assert C.rotate_profile() is not None


def test_profiles_stay_short():
    """yt-dlp выгребает весь список клиентов и сливает форматы, раннего выхода нет

    Значит лишний клиент в профиле — плата на каждом треке, а не запасной путь.
    Замер на 30 треках: пять клиентов дали медиану 6592 мс, два — 2257 мс
    при той же играбельности 30/30
    """
    for name, clients in C._CLIENT_PROFILES:
        assert len(clients) <= 2, f'{name}: {len(clients)} клиентов, каждый платится на каждом треке'


def test_useless_clients_are_absent():
    """Клиенты, не отдавшие ничего на замере, не должны возвращаться в профили"""
    used = {c for _, clients in C._CLIENT_PROFILES for c in clients}
    assert 'ios' not in used, 'ios отдал 0 форматов из 30 и стоил 1.3 с на трек'
    assert 'android_vr' not in used, 'ссылки android_vr сессионно отвечают 403'


class _Recorder:
    """Дубль ytdl.extract_info: отдаёт заготовленные ответы и помнит профиль вызова"""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, url, download=False):
        self.calls.append((url, C.active_profile()))
        return self.results.pop(0)


def test_extract_info_rotates_on_empty(monkeypatch):
    """Пустой ответ — это чаще нехватка форматов у клиента, а не мёртвое видео"""
    C._active, C._last_rotate = 0, 0.0
    C._apply_profile(0)
    rec = _Recorder([None, {'url': 'stream'}])
    monkeypatch.setattr(C.ytdl, 'extract_info', rec)

    assert C.extract_info('https://x/watch?v=1') == {'url': 'stream'}
    assert [p for _, p in rec.calls] == [C._CLIENT_PROFILES[0][0], C._CLIENT_PROFILES[1][0]]


def test_extract_info_keeps_profile_on_success(monkeypatch):
    C._active, C._last_rotate = 0, 0.0
    C._apply_profile(0)
    rec = _Recorder([{'url': 'stream'}])
    monkeypatch.setattr(C.ytdl, 'extract_info', rec)

    assert C.extract_info('https://x/watch?v=1') == {'url': 'stream'}
    assert len(rec.calls) == 1
    assert C.active_profile() == C._CLIENT_PROFILES[0][0]


def test_extract_info_gives_up_when_cooldown_blocks(monkeypatch):
    """Битое видео не должно прокручивать профили: второй попытки нет"""
    C._active = 0
    C._last_rotate = C.time.monotonic()
    C._apply_profile(0)
    rec = _Recorder([None])
    monkeypatch.setattr(C.ytdl, 'extract_info', rec)

    assert C.extract_info('https://x/watch?v=1') is None
    assert len(rec.calls) == 1
    assert C.active_profile() == C._CLIENT_PROFILES[0][0]


def test_extract_info_rotation_can_be_disabled(monkeypatch):
    """warmup и подобные вызовы не должны сдвигать профиль всему процессу"""
    C._active, C._last_rotate = 0, 0.0
    C._apply_profile(0)
    rec = _Recorder([None])
    monkeypatch.setattr(C.ytdl, 'extract_info', rec)

    assert C.extract_info('https://x/watch?v=1', rotate_on_empty=False) is None
    assert len(rec.calls) == 1
    assert C.active_profile() == C._CLIENT_PROFILES[0][0]


def _needs_gvs_token(client_name: str) -> bool:
    """Требует ли клиент GVS PO Token — по данным самого yt-dlp, а не по списку

    Так проверка останется верной, если YouTube поменяет политику, а yt-dlp
    её обновит
    """
    from yt_dlp.extractor.youtube._base import INNERTUBE_CLIENTS
    policy = (INNERTUBE_CLIENTS.get(client_name) or {}).get('GVS_PO_TOKEN_POLICY')
    if policy is None:
        return False
    if isinstance(policy, dict):
        policy = next(iter(policy.values()))
    return bool(getattr(policy, 'required', False))


def test_есть_путь_без_токена():
    """Провайдера PO Token в проекте нет намеренно, и ставить его отвергнуто
    замером. Значит хотя бы один профиль обязан содержать клиента, которому
    токен не нужен, иначе играть будет нечем

    Профиль может смешивать клиентов: yt-dlp сольёт их форматы, и достаточно
    одного безтокенного, чтобы получить поток
    """
    with_free = [name for name, clients in C._CLIENT_PROFILES
                 if any(not _needs_gvs_token(c) for c in clients)]
    assert with_free, 'ни в одном профиле нет клиента, работающего без токена'




def test_отвергнутые_замером_клиенты_не_возвращаются():
    used = {c for _, clients in C._CLIENT_PROFILES for c in clients}
    assert 'tv_simply' not in used, 'замер: 25/30 и 0 opus, хуже web_music по всем осям'
    assert 'tv' not in used, 'замер: 0/30, не отдал ни одного формата'
