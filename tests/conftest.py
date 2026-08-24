"""Общие фикстуры и заглушки

Сетевые тесты помечены network и по умолчанию отключены в pytest.ini,
запуск: pytest -m network
"""
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FakeVoiceClient:
    """Минимальный VoiceClient: только то, от чего зависит логика кога"""

    def __init__(self, playing=False, paused=False, channel=None):
        self._playing = playing
        self._paused = paused
        self.channel = channel
        self._source = None
        self.played = []
        self.stopped = 0
        self.paused_calls = 0

    @property
    def source(self):
        return self._source

    @source.setter
    def source(self, value):
        # discord.py: присваивание уходит в AudioPlayer.set_source(), а он делает
        # pause() -> подмена -> resume() безусловно, снимая паузу
        self._source = value
        self._paused = False
        self._playing = True

    def is_playing(self):
        return self._playing

    def is_paused(self):
        return self._paused

    def play(self, source, *, after=None):
        if self._playing:
            raise RuntimeError('Already playing audio.')
        self._playing = True
        self._source = source
        self.played.append(source)

    def stop(self):
        self.stopped += 1
        self._playing = False
        self._paused = False

    def pause(self):
        self.paused_calls += 1
        self._paused = True
        self._playing = False

    def resume(self):
        self._paused = False
        self._playing = True


class FakeGuild:
    def __init__(self, gid=1, name='test-guild', voice_client=None):
        self.id = gid
        self.name = name
        self.voice_client = voice_client


class SlottedInteraction:
    """Взаимодействие после defer

    __slots__ намеренно: настоящий discord.Interaction тоже слотированный и
    чужих атрибутов не принимает. Дубль без слотов это скрывал, и код падал
    только в бою
    """

    __slots__ = ('id', 'edited', 'fail', 'response', 'dismissed')

    def __init__(self, iid=1, fail=False, deferred=True):
        self.id = iid
        self.edited = []
        self.dismissed = False
        self.fail = fail
        # заглушка появляется только после defer(); у недефёрнутой команды
        # исходного ответа нет и редактировать нечего
        self.response = types.SimpleNamespace(is_done=lambda: deferred)

    async def edit_original_response(self, **kwargs):
        if self.fail:
            import discord
            raise discord.HTTPException(type('R', (), {'status': 404, 'reason': ''})(), 'gone')
        self.edited.append(kwargs)
        msg = types.SimpleNamespace(id=900 + self.id, deleted=False, edits=[])

        async def _delete():
            msg.deleted = True

        async def _edit(**kw):
            msg.edits.append(kw)

        msg.delete, msg.edit = _delete, _edit
        return msg

    async def delete_original_response(self):
        """Убрать заглушку «бот думает»: ког закрывает взаимодействие так"""
        if self.fail:
            import discord
            raise discord.HTTPException(type('R', (), {'status': 404, 'reason': ''})(), 'gone')
        self.dismissed = True


def _make_msg(content=None, *, embed=None, view=None,
              ephemeral=False, delete_after=None):
    FakeCtx._next_id += 1
    msg = types.SimpleNamespace(id=FakeCtx._next_id, content=content, embed=embed,
                                view=view, edits=[], deleted=False,
                                ephemeral=ephemeral, delete_after=delete_after)

    async def _edit(**kwargs):
        msg.edits.append(kwargs)

    async def _delete():
        msg.deleted = True

    msg.edit = _edit
    msg.delete = _delete
    return msg


class FakeChannel:
    """Прямая отправка в канал

    Держим отдельно от FakeCtx.send: у слэш-команды тот шлёт followup через
    вебхук взаимодействия, который умирает через 15 минут, и путать эти два
    пути нельзя
    """

    def __init__(self):
        self.sent = []

    async def send(self, content=None, *, embed=None, view=None,
                   ephemeral=False, delete_after=None):
        msg = _make_msg(content, embed=embed, view=view,
                        ephemeral=ephemeral, delete_after=delete_after)
        self.sent.append(msg)
        return msg


class FakeCtx:
    def __init__(self, guild=None, voice_client=None, author_id=42):
        self.guild = guild or FakeGuild()
        self.voice_client = voice_client
        self.interaction = None
        self.sent = []
        self.deferred_ephemeral = None
        self.channel = FakeChannel()
        self.author = types.SimpleNamespace(id=author_id, voice=None)

    _next_id = 1000

    async def send(self, content=None, *, embed=None, view=None,
                   ephemeral=False, delete_after=None):
        """ephemeral и delete_after запоминаем: ког ими управляет тем, что
        остаётся в канале, и это надо проверять"""
        msg = _make_msg(content, embed=embed, view=view,
                        ephemeral=ephemeral, delete_after=delete_after)
        self.sent.append(msg)
        return msg

    async def reply(self, content=None, *, embed=None, view=None,
                    mention_author=None, delete_after=None):
        """Ответ на сообщение с командой — обычное сообщение канала со ссылкой

        Кладём в channel.sent, а не в sent: это не followup вебхука, и путать
        их нельзя. Ссылку запоминаем — ради неё ответ и делается
        """
        msg = _make_msg(content, embed=embed, view=view, delete_after=delete_after)
        msg.reference = getattr(self, 'message', 'сообщение с командой')
        self.channel.sent.append(msg)
        return msg

    async def defer(self, *, ephemeral=False):
        """Настоящий ctx.defer принимает ephemeral, и от него зависит,
        останется ли ответ в канале: дубль обязан принимать его тоже"""
        self.deferred_ephemeral = ephemeral


class FakeBot:
    def __init__(self, guild=None):
        self._guild = guild or FakeGuild()
        self.guilds = [self._guild]
        self.loop = None
        self.user = types.SimpleNamespace(id=999)

    def get_guild(self, gid):
        return self._guild if self._guild.id == gid else None


@pytest.fixture(autouse=True)
def _no_artist_cache_writes(tmp_path, monkeypatch):
    """Не давать тестам писать в боевой log/.artist-ids.json

    _remember_artist сохраняет browseId на диск, и тест с заглушкой оставлял там
    выдуманный идентификатор: в кэше нашлась запись 'X' -> 'UC_late'. Дальше бот
    брал её как настоящую и ходил с ней в get_artist
    """
    from audio.youtube import search
    monkeypatch.setattr(search, '_ARTIST_CACHE_PATH', tmp_path / '.artist-ids.json')
    yield


@pytest.fixture
def voice():
    return FakeVoiceClient()


@pytest.fixture
def guild(voice):
    return FakeGuild(voice_client=voice)


@pytest.fixture
def ctx(guild, voice):
    return FakeCtx(guild=guild, voice_client=voice)


@pytest.fixture
def cog(guild):
    from cogs.music import Music
    return Music(FakeBot(guild))


def track(title='T', artist='A', url='https://example.com/x', duration=0.0, thumbnail=''):
    from audio.track import Track
    return Track(url=url, title=title, artist=artist, duration=duration, thumbnail=thumbnail)
