import asyncio
import logging
import math
import random
import threading
import time
from collections import deque

import discord
import yt_dlp as youtube_dl
from discord import app_commands
from discord.ext import commands

from audio import Track, YTDLSource, extract, rotate_stream_client
from logs import close_guild_session, open_guild_session, session_log
from widgets import (
    PROGRESS_TICK_SECONDS,
    QueueView,
    build_added_playlist_embed,
    build_added_track_embed,
    build_current_track_embed,
    build_now_playing_embed,
    format_time,
    format_track_label,
    plain_error,
)


_log = logging.getLogger('audio').info


# хвост трека, который считается концом: перемотка сюда = пропуск,
# финальный прогресс-бар дорисовывается до 100%
END_OF_TRACK_EPSILON_SECONDS = 1

# авто-выход при одиночестве в голосовом канале
ALONE_TIMEOUT_SECONDS = 300
# авто-выход при простое: пустая очередь и ничего не играет
IDLE_TIMEOUT_SECONDS = 300

# Сколько идентификаторов взаимодействий и сообщений помним. Взаимодействие
# живёт 15 минут, карточка удаляется в пределах одного трека — вечно копить
# их незачем, а бот работает сутками
ID_MEMORY = 512

# сколько раз перевыдать стрим-URL, если ffmpeg не смог его открыть;
# googlevideo отдаёт транзиентные 403, свежая ссылка обычно играет
MAX_PLAY_ATTEMPTS = 2
# текстовый вызов эфемерных сообщений не умеет — убираем их сами
EPHEMERAL_FALLBACK_SECONDS = 15
# короткие подсказки об ошибке; ошибки загрузки остаются как след
ERROR_MESSAGE_SECONDS = 15


def _parse_time_position(text: str) -> float | None:
    """'90', '1:30', '1:02:03' -> секунды, None при невалидном вводе"""
    parts = text.strip().split(':')
    try:
        if len(parts) == 1:
            value = float(parts[0])
        elif len(parts) == 2:
            value = int(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            value = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        else:
            return None
    except ValueError:
        return None
    # float() принимает 'nan'/'inf', они уезжают в -ss и роняют ffmpeg
    return value if math.isfinite(value) else None


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queues: dict[int, deque[Track]] = {}
        self._prefetched: dict[int, tuple[Track, YTDLSource]] = {}
        # заготовки в полёте: без этого _play_next_locked не знает, что трек уже
        # резолвится, и запускает второй резолв того же трека
        self._prefetching: dict[int, tuple[Track, asyncio.Task]] = {}
        self._current: dict[int, Track] = {}
        self._loop_modes: dict[int, str] = {}
        self._skip_loop_once: set[int] = set()
        self._played: dict[int, list[Track]] = {}
        self._np_msg: dict[int, discord.Message] = {}
        self._np_source: dict[int, YTDLSource] = {}
        self._np_task: dict[int, asyncio.Task] = {}
        self._track_start: dict[int, float] = {}
        self._pause_start: dict[int, float] = {}
        self._pause_total: dict[int, float] = {}
        self._alone_tasks: dict[int, asyncio.Task] = {}
        self._auto_paused: set[int] = set()
        self._idle_tasks: dict[int, asyncio.Task] = {}
        self._play_locks: dict[int, asyncio.Lock] = {}
        self._bg_tasks: set[asyncio.Task] = set()
        # id взаимодействий, у которых заглушка «бот думает» уже заменена.
        # dict как упорядоченное множество: старые записи вытесняются
        self._answered: dict[int, None] = {}
        # id сообщений, которыми заменена заглушка: удалять их нельзя, иначе
        # последующие followup'ы снова начнут ссылаться в пустоту
        self._original_msgs: dict[int, None] = {}
        # доигравшая карточка, ждёт удаления при переходе к следующему треку
        # (сообщение, подпись) — подпись нужна, чтобы свернуть исходный
        # ответ взаимодействия вместо удаления
        self._np_finished: dict[int, tuple] = {}

    # --- инфраструктура ---------------------------------------------------

    def get_queue(self, guild_id: int) -> deque[Track]:
        return self.queues.setdefault(guild_id, deque())

    def _play_lock(self, guild_id: int) -> asyncio.Lock:
        return self._play_locks.setdefault(guild_id, asyncio.Lock())

    def _spawn(self, coro) -> asyncio.Task:
        """create_task + удержание ссылки: loop хранит только слабую, задачу может собрать GC"""
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task

    async def _private(self, ctx, content=None, **kwargs):
        """Ответ, видимый только вызвавшему

        Управляющие команды не должны оставлять следов: в канале копятся только
        добавления в очередь и карточка текущего трека
        """
        return await self._respond(ctx, content, private=True, **kwargs)

    async def _transient(self, ctx, content=None, *, seconds=None, **kwargs):
        """Публичный ответ, который сам уберётся

        Для сообщений, не привязанных к команде: их шлёт воспроизведение,
        и взаимодействие к тому моменту может быть уже просрочено
        """
        return await self._respond(
            ctx, content, delete_after=seconds or ERROR_MESSAGE_SECONDS, **kwargs)

    async def _ensure_deferred(self, ctx, *, private: bool = False):
        """private — заглушка «бот думает» тоже должна быть личной

        Иначе публичная заглушка останется висеть в канале, а ответ уйдёт
        эфемерным: команда отработает, а в истории останется мусор
        """
        if ctx.interaction is not None and not ctx.interaction.response.is_done():
            await ctx.defer(ephemeral=private)

    @staticmethod
    def _remember(store: dict, key: int) -> None:
        """Запомнить id, вытеснив самые старые сверх ID_MEMORY"""
        store[key] = None
        while len(store) > ID_MEMORY:
            del store[next(iter(store))]

    async def _respond(self, ctx, content=None, *, private: bool = False,
                       delete_after: float | None = None, **kwargs):
        """Отправить сообщение пользователю

        private — видно только вызвавшему. Эфемерные сообщения существуют
        только у слэш-команд; при текстовом вызове discord.py их молча
        игнорирует, поэтому там подстраховываемся удалением по таймеру.
        В канале в итоге остаются только добавления в очередь и карточка
        текущего трека

        После ctx.defer() исходным ответом взаимодействия остаётся заглушка
        «бот думает». Если её не заменить, каждое наше сообщение уходит как
        followup и привязывается к этой заглушке, а Discord показывает над ним
        «Не удается загрузить сообщение». Поэтому первое сообщение редактирует
        исходный ответ, остальные идут обычным путём
        """
        inter = ctx.interaction
        if private:
            if inter is not None:
                kwargs['ephemeral'] = True
            elif delete_after is None:
                delete_after = EPHEMERAL_FALLBACK_SECONDS
        if delete_after is not None:
            kwargs['delete_after'] = delete_after
        # Заглушка существует только после defer(). На неотвеченном взаимодействии
        # edit_original_response уходит в Discord, возвращает 404 и сжигает
        # трёхсекундное окно ответа — команда падает с Unknown interaction.
        # Состояние держим у себя: discord.Interaction на __slots__ и чужих
        # атрибутов не принимает
        if (inter is not None and inter.response.is_done()
                and inter.id not in self._answered):
            self._remember(self._answered, inter.id)
            edit_kwargs = {k: v for k, v in kwargs.items()
                           if k not in ('ephemeral', 'delete_after')}
            try:
                msg = await inter.edit_original_response(content=content, **edit_kwargs)
            except discord.HTTPException:
                pass
            else:
                if getattr(msg, 'id', None) is not None:
                    self._remember(self._original_msgs, msg.id)
                # ephemeral в правке бессилен — эфемерность задана дефёром, — а
                # delete_after правка не понимает вовсе. Без этого служебный
                # ответ, занявший слот заглушки, остаётся в канале навсегда
                if delete_after is not None:
                    self._spawn(self._delete_later(msg, delete_after))
                return msg
        return await ctx.send(content, **kwargs)

    @staticmethod
    async def _delete_later(msg, delay: float) -> None:
        """Убрать сообщение через delay секунд, не мешая вызвавшему"""
        await asyncio.sleep(delay)
        try:
            await msg.delete()
        except discord.HTTPException:
            pass

    async def _send_card(self, ctx, embed):
        """Карточка «сейчас играет» уходит прямо в канал, мимо взаимодействия

        ctx.send у слэш-команды шлёт followup через вебхук взаимодействия, а его
        токен живёт 15 минут от команды. Карточка живёт дольше: трек, начавшийся
        под конец окна, терял и правку, и удаление — прогресс замирал, а
        следующий трек не мог её убрать. Обычное сообщение канала правится и
        удаляется всегда
        """
        return await ctx.channel.send(embed=embed)

    async def _added_card(self, ctx, embed):
        """Карточка добавления: видно, кто и чем её вызвал

        Слэш-команда — followup'ом взаимодействия: над ним Discord рисует
        «использует /play». Править исходный ответ нельзя, правка не проксирует
        внешние картинки и обложка исчезает через секунду (проверено на Yandex
        и VK, разные CDN, поведение одинаковое). Followup — создание нового
        сообщения, а не правка, и 15-минутный токен вебхука карточке не мешает:
        её никогда не правят и не удаляют.

        Текстовая команда — ответом на сообщение с командой, там взаимодействия
        нет вовсе.

        Заглушку закрывать отдельно НЕ надо: первый followup после дефёра сам
        занимает её слот, то есть карточка и есть исходный ответ. Пока это
        делалось, `delete_original_response` стирал саму карточку — она
        появлялась и тут же исчезала
        """
        if ctx.interaction is None:
            return await ctx.reply(embed=embed, mention_author=False)
        # Помечаем взаимодействие отвеченным ДО отправки: иначе следующий
        # _respond решит, что заглушка ещё висит, и затрёт карточку правкой
        self._remember(self._answered, ctx.interaction.id)
        card = await ctx.send(embed=embed)
        # исходный ответ удалять нельзя: на него ссылаются followup'ы
        if getattr(card, 'id', None) is not None:
            self._remember(self._original_msgs, card.id)
        return card

    def _resync_prefetch(self, gid: int, vc=None):
        """Перезапустить заготовку, если голова очереди сменилась

        Заготовка делается один раз при старте трека — для той головы, что была
        в тот момент. Вставка вперёд и перемешивание её обесценивают, а
        добавление в пустую очередь оставляет бота вовсе без заготовки:
        следующий трек придётся резолвить с нуля, и пользователь ждёт
        """
        queue = self.get_queue(gid)
        if not queue:
            return
        head = queue[0]
        ready = self._prefetched.get(gid)
        pending = self._prefetching.get(gid)
        if (ready is not None and ready[0] is head) or (pending is not None and pending[0] is head):
            return  # заготовка уже про эту голову
        self._drop_prefetched(gid)
        if vc is None:
            guild = self.bot.get_guild(gid)
            vc = guild.voice_client if guild is not None else None
        # пока ничего не играет, заготовка не нужна: трек возьмёт _play_next_locked
        if vc is not None and (vc.is_playing() or vc.is_paused()):
            self._spawn(self._prefetch(gid))

    def _drop_prefetched(self, guild_id: int):
        """Снять заготовку: и готовую, и ту, что ещё резолвится"""
        prefetched = self._prefetched.pop(guild_id, None)
        if prefetched is not None:
            prefetched[0].drop_resolved()
            try:
                prefetched[1].cleanup()
            except Exception:
                pass
        # голова очереди изменилась, значит незавершённая заготовка уже не про неё
        pending = self._prefetching.pop(guild_id, None)
        if pending is not None and not pending[1].done():
            pending[1].cancel()

    @staticmethod
    def _schedule_source_cleanup(source, delay: float = 2.0):
        """Погасить ffmpeg и поток буферизации через delay секунд"""
        def _run():
            time.sleep(delay)
            try:
                source.cleanup()
            except Exception:
                pass
        threading.Thread(target=_run, name='src-cleanup', daemon=True).start()

    async def cog_unload(self):
        """Снять таймеры, источники и голосовые подключения: иначе ffmpeg переживает перезагрузку кога"""
        # таймеры живут отдельно от очередей: гильдия, где бот только сидит
        # в канале, не попадает ни в queues, ни в _prefetched, ни в _np_source.
        # Её таймер пережил бы перезагрузку кога и потом отключил бы уже чужую
        # сессию
        for gid in (set(self.queues) | set(self._prefetched) | set(self._np_source)
                    | set(self._alone_tasks) | set(self._idle_tasks)):
            self._cancel_alone_timer(gid)
            self._cancel_idle_timer(gid)
            task = self._np_task.pop(gid, None)
            if task is not None and not task.done():
                task.cancel()
            self._drop_prefetched(gid)
            source = self._np_source.pop(gid, None)
            if source is not None:
                try:
                    source.cleanup()
                except Exception:
                    pass
        for task in list(self._bg_tasks):
            task.cancel()
        for guild in list(self.bot.guilds):
            vc = guild.voice_client
            if vc is not None:
                try:
                    await vc.disconnect(force=True)
                except Exception:
                    pass
            close_guild_session(guild.id)

    # --- учёт времени -----------------------------------------------------

    def _elapsed(self, gid: int) -> float:
        start = self._track_start.get(gid)
        if start is None:
            return 0.0
        elapsed = time.monotonic() - start - self._pause_total.get(gid, 0.0)
        pause_start = self._pause_start.get(gid)
        if pause_start is not None:
            elapsed -= time.monotonic() - pause_start
        return max(0.0, elapsed)

    def _playback_state(self, gid: int) -> str:
        guild = self.bot.get_guild(gid)
        vc = guild.voice_client if guild is not None else None
        if vc is None:
            return 'stopped'
        if vc.is_paused():
            return 'paused'
        if vc.is_playing():
            return 'playing'
        return 'stopped'

    # --- виджет «сейчас играет» -------------------------------------------

    async def _refresh_now_playing(self, gid: int):
        msg = self._np_msg.get(gid)
        source = self._np_source.get(gid)
        track = self._current.get(gid)
        if msg is None or source is None or track is None:
            return
        embed = build_now_playing_embed(
            track, source, self._elapsed(gid), state=self._playback_state(gid),
            repeat=self._loop_modes.get(gid, 'off'),
        )
        try:
            await msg.edit(embed=embed)
        except discord.HTTPException:
            pass

    async def _now_playing_updater(self, gid: int, source: YTDLSource, track: Track):
        duration = float(source.data.get('duration') or 0)
        try:
            while True:
                await asyncio.sleep(PROGRESS_TICK_SECONDS)
                msg = self._np_msg.get(gid)
                if msg is None or self._current.get(gid) is not track:
                    return
                elapsed = self._elapsed(gid)
                try:
                    await msg.edit(embed=build_now_playing_embed(
                        track, source, elapsed, state=self._playback_state(gid),
                        repeat=self._loop_modes.get(gid, 'off'),
                    ))
                except discord.HTTPException:
                    return
                if duration > 0 and elapsed >= duration:
                    return
        except asyncio.CancelledError:
            pass

    async def _stop_now_playing(self, gid: int, *, final_fill: bool = False):
        task = self._np_task.pop(gid, None)
        if task is not None and not task.done():
            task.cancel()
        msg = self._np_msg.pop(gid, None)
        source = self._np_source.pop(gid, None)
        if final_fill and msg is not None and source is not None:
            track = self._current.get(gid)
            if track is not None:
                duration = float(source.data.get('duration') or 0)
                elapsed = self._elapsed(gid)
                if duration > 0:
                    if duration - elapsed <= END_OF_TRACK_EPSILON_SECONDS:
                        elapsed = duration
                    else:
                        elapsed = min(elapsed, duration)
                embed = build_now_playing_embed(
                    track, source, elapsed, state='stopped',
                    repeat=self._loop_modes.get(gid, 'off'))
                try:
                    await msg.edit(embed=embed)
                except discord.HTTPException as exc:
                    # карточка останется с кнопкой «играет»: видно пользователю
                    session_log(gid, f'финальный кадр карточки не лёг: {exc!r}')
            # карточку уберёт следующий трек; если его не будет, она останется
            # финальным снимком
            self._np_finished[gid] = (msg, format_track_label(track))
        self._track_start.pop(gid, None)
        self._pause_start.pop(gid, None)
        self._pause_total.pop(gid, None)

    # --- ядро воспроизведения ---------------------------------------------

    def _after_play(self, ctx, error, source=None):
        """Колбэк discord.py, выполняется в потоке AudioPlayer

        Блокировать поток нельзя: discord.py вызывает колбэк до source.cleanup(),
        ожидание резолва следующего трека держало бы старый ffmpeg живым
        """
        gid = ctx.guild.id
        reason = str(error) if error else (source.failure_reason() if source is not None else None)
        # ротация профиля осмысленна только для потоков YouTube: Yandex и
        # SoundCloud идут через свои клиенты и на этот профиль не смотрят
        from_youtube = bool(source is not None
                            and (source.data.get('extractor') or '').startswith('youtube'))
        if reason:
            print(f'Player error: {reason}')
            session_log(gid, f'player error: {reason}')
        fut = asyncio.run_coroutine_threadsafe(
            self._advance(ctx, reason, from_youtube=from_youtube), self.bot.loop)

        def _report(f):
            try:
                exc = f.exception()
            except asyncio.CancelledError:
                return
            if exc is not None:
                print(f'play_next error: {exc!r}')
                session_log(gid, f'play_next error: {exc!r}')

        fut.add_done_callback(_report)

    async def _advance(self, ctx, reason: str | None, *, from_youtube: bool = False):
        """Обработать итог трека и перейти к следующему

        Сбой открытия потока чаще всего транзиентный: googlevideo отдаёт 403 на
        конкретную ссылку, а перевыданная играет. Поэтому трек возвращается в
        голову очереди со сброшенным кэшем, и только исчерпав попытки — уходит
        с сообщением пользователю
        """
        gid = ctx.guild.id
        track = self._current.get(gid)
        if not reason:
            if track is not None:
                track._play_attempts = 0
            await self._play_next(ctx)
            return

        if track is not None and track._play_attempts < MAX_PLAY_ATTEMPTS:
            track._play_attempts += 1
            track.drop_resolved()
            self._drop_prefetched(gid)  # соседняя ссылка обычно протухла так же
            # перевыдача тем же клиентом даст тот же отказ, если YouTube перестал
            # его обслуживать; поэтому перед повтором пробуем следующий профиль
            if from_youtube:
                rotated = rotate_stream_client()
                if rotated:
                    session_log(gid, f'stream client -> {rotated}')
            self.get_queue(gid).appendleft(track)
            # Трек уже в очереди, и в _current его оставлять нельзя:
            # _play_next_locked обработает его второй раз — при repeat=track
            # вернёт в голову ещё раз, при repeat=queue положит в _played,
            # и трек навсегда удвоится в круге
            self._current.pop(gid, None)
            session_log(gid, f'retry {track._play_attempts}/{MAX_PLAY_ATTEMPTS}: '
                             f'{format_track_label(track)} ({reason})')
        else:
            name = format_track_label(track) if track is not None else 'трек'
            if track is not None:
                track._play_attempts = 0
                # Попытки исчерпаны. При repeat=track _play_next_locked вернул бы
                # мёртвый трек в голову очереди, и он крутился бы бесконечно:
                # счётчик попыток обнуляется на каждом круге
                if self._loop_modes.get(gid) == 'track':
                    self._skip_loop_once.add(gid)
            try:
                await self._transient(ctx, f'Не удалось воспроизвести «{name}» — {plain_error(reason)}')
            except discord.HTTPException:
                pass
        await self._play_next(ctx)

    async def _drop_finished_card(self, gid: int):
        """Убрать доигравшую карточку перед показом следующей

        Плейлист на сотню треков иначе оставляет сотню мёртвых прогресс-баров.

        Исходный ответ взаимодействия удалять нельзя: на него ссылаются
        followup'ы, и после удаления Discord пишет «Не удается загрузить
        сообщение». Карточкой он больше не бывает — команды отвечают текстом
        до неё, — но проверка остаётся страховкой: если это правило когда-то
        нарушат, карточка просто не удалится, а не сломает ссылки
        """
        item = self._np_finished.pop(gid, None)
        if item is None:
            return
        msg, label = item
        if getattr(msg, 'id', None) in self._original_msgs:
            return
        try:
            await msg.delete()
        except discord.HTTPException as exc:
            # Отказ здесь оставляет в канале мёртвый прогресс-бар навсегда,
            # и молча это тянулось долго. В журнал — чтобы следующий такой
            # случай был виден без скриншота
            session_log(gid, f'карточка «{label}» не удалилась: {exc!r}')

    async def _play_next(self, ctx):
        """Перейти к следующему треку

        Лок на гильдию обязателен: VoiceClient.stop() зануляет _player, из-за чего
        is_playing() сразу False, и параллельный вызов создал бы второй AudioPlayer
        """
        async with self._play_lock(ctx.guild.id):
            await self._play_next_locked(ctx)

    async def _start_if_idle(self, ctx):
        """Запустить воспроизведение, если плеер простаивает; проверка под локом"""
        async with self._play_lock(ctx.guild.id):
            vc = ctx.voice_client
            if vc is None or vc.is_playing() or vc.is_paused():
                return
            await self._play_next_locked(ctx)

    async def _play_next_locked(self, ctx):
        gid = ctx.guild.id
        await self._stop_now_playing(gid, final_fill=True)
        queue = self.get_queue(gid)
        if ctx.voice_client is None:
            self._drop_prefetched(gid)
            self._current.pop(gid, None)
            return

        loop_mode = self._loop_modes.get(gid, 'off')
        if loop_mode == 'track':
            if gid in self._skip_loop_once:
                self._skip_loop_once.discard(gid)
            else:
                current = self._current.get(gid)
                if current is not None:
                    queue.appendleft(current)
        elif loop_mode == 'queue':
            current = self._current.get(gid)
            if current is not None:
                # сбрасываем кэш: стрим-URL к следующему кругу протухнет
                current.drop_resolved()
                self._played.setdefault(gid, []).append(current)
            if not queue and self._played.get(gid):
                queue.extend(self._played[gid])
                self._played[gid].clear()

        t_total = time.perf_counter()
        chosen: tuple[Track, YTDLSource] | None = None
        used_prefetched = False

        # Заготовка того же трека могла ещё резолвиться. Без ожидания мы
        # зарезолвим его повторно: лишний подбор, лишний extract_info и лишний
        # поднятый ffmpeg, а при 5% блокировок YouTube ещё и вдвое больше запросов.
        # asyncio.wait, а не await: ошибка или отмена заготовки не должна ломать переход
        pending = self._prefetching.get(gid)
        if pending is not None and queue and queue[0] is pending[0]:
            await asyncio.wait({pending[1]})

        prefetched = self._prefetched.pop(gid, None)
        if prefetched and queue and queue[0] is prefetched[0]:
            queue.popleft()
            chosen = prefetched
            used_prefetched = True
        elif prefetched:
            try:
                prefetched[1].cleanup()  # голова сменилась, заготовка не про неё
            except Exception:
                pass

        # Источник годится, только когда в буфере уже есть звук. Плеер
        # тактируется абсолютным временем от play(), поэтому старт с пустым
        # буфером превращает заминку ffmpeg (переподключения на 403 от CDN)
        # не в задержку, а в тишину поверх идущего отсчёта и догонку пачкой
        t_source = time.perf_counter()
        while True:
            if chosen is None:
                if not queue:
                    break
                track = queue.popleft()
                used_prefetched = False
                t_source = time.perf_counter()
                try:
                    chosen = (track, await track.make_source(loop=self.bot.loop))
                except Exception as exc:
                    print(f'failed to load {track.title!r}: {exc!r}')
                    session_log(gid, f'failed to load {track.title!r}: {exc!r}')
                    await self._transient(ctx, f'Пропускаю «{track.title}» (ошибка загрузки)')
                    continue

            loop = asyncio.get_running_loop()
            t_ready = time.perf_counter()
            ready = await loop.run_in_executor(None, chosen[1].wait_ready)
            # Разбивка нужна, чтобы не гадать, где ушло время: поднять ffmpeg
            # стоит единицы миллисекунд, всё остальное — набор префилла по сети.
            # У заготовки и то и другое уже позади, там мерить нечего
            if not used_prefetched:
                _log(f'source {(t_ready - t_source) * 1000:.0f} ms + '
                     f'prefill {(time.perf_counter() - t_ready) * 1000:.0f} ms '
                     f'for {chosen[0].title!r} (ready={ready})')
            if ready:
                break

            track, source = chosen
            chosen = None
            reason = source.failure_reason() or 'поток не начался'
            print(f'stream not ready for {track.title!r}: {reason}')
            session_log(gid, f'stream not ready for {track.title!r}: {reason}')
            try:
                source.cleanup()
            except Exception:
                pass
            from_youtube = (source.data.get('extractor') or '').startswith('youtube')
            if track._play_attempts < MAX_PLAY_ATTEMPTS:
                track._play_attempts += 1
                track.drop_resolved()
                if from_youtube:
                    rotate_stream_client()
                queue.appendleft(track)
            else:
                await self._transient(ctx, f'Пропускаю «{track.title}» (поток не открылся)')

        if chosen is None:
            self._current.pop(gid, None)
            self._start_idle_timer(gid)
            return

        # резолв идёт по сети, за это время бота могли выдернуть из канала
        if ctx.voice_client is None:
            try:
                chosen[1].cleanup()
            except Exception:
                pass
            self._current.pop(gid, None)
            return

        track, source = chosen
        self._current[gid] = track
        self._cancel_idle_timer(gid)
        ctx.voice_client.play(source, after=lambda e: self._after_play(ctx, e, source))
        _log(f'play_next -> playing {track.title!r} in {(time.perf_counter() - t_total) * 1000:.0f} ms (prefetched={used_prefetched})')
        session_log(gid, f'playing: {format_track_label(track)} (prefetched={used_prefetched})')

        self._track_start[gid] = time.monotonic()
        self._pause_total[gid] = 0.0
        self._pause_start.pop(gid, None)
        await self._drop_finished_card(gid)
        msg = await self._send_card(ctx, build_now_playing_embed(
            track, source, 0.0, state='playing',
            repeat=self._loop_modes.get(gid, 'off')))
        self._np_msg[gid] = msg
        self._np_source[gid] = source
        self._np_task[gid] = self._spawn(self._now_playing_updater(gid, source, track))

        if loop_mode != 'track':
            self._spawn(self._prefetch(gid))

    async def _prefetch(self, guild_id: int):
        if guild_id in self._prefetched or guild_id in self._prefetching:
            return
        queue = self.get_queue(guild_id)
        if not queue:
            return
        track = queue[0]
        self._prefetching[guild_id] = (track, asyncio.current_task())
        t0 = time.perf_counter()
        try:
            try:
                source = await track.make_source(loop=self.bot.loop)
            except Exception as exc:
                print(f'prefetch error for {track.title!r}: {exc!r}')
                return
            # очередь могла измениться, пока шёл резолв
            cur_queue = self.get_queue(guild_id)
            if cur_queue and cur_queue[0] is track and guild_id not in self._prefetched:
                self._prefetched[guild_id] = (track, source)
                _log(f'prefetched {track.title!r} in {(time.perf_counter() - t0) * 1000:.0f} ms')
            else:
                try:
                    source.cleanup()
                except Exception:
                    pass
        finally:
            self._prefetching.pop(guild_id, None)

    # --- подключение ------------------------------------------------------

    @commands.hybrid_command(description='Подключиться к голосовому каналу')
    @app_commands.describe(channel='Канал для подключения (необязательно)')
    async def join(self, ctx, *, channel: discord.VoiceChannel = None):
        """Подключиться к голосовому каналу"""
        await self._ensure_deferred(ctx, private=True)
        if channel is None:
            if ctx.author.voice is None:
                return await self._private(ctx, 'Вы не в голосовом канале')
            channel = ctx.author.voice.channel
        if ctx.voice_client is not None:
            await ctx.voice_client.move_to(channel)
            session_log(ctx.guild.id, f'moved to voice channel: {channel.name}')
        else:
            await channel.connect(self_deaf=True)
            open_guild_session(ctx.guild.id, ctx.guild.name)
            session_log(ctx.guild.id, f'joined voice channel: {channel.name}')
        await self._private(ctx, f'Подключилась к {channel.name}')

    async def _reset_session(self, gid: int):
        self.get_queue(gid).clear()
        self._drop_prefetched(gid)
        # final_fill обязателен: сюда приходят все способы закончить
        # воспроизведение, и карточка должна перейти в остановленное состояние.
        # Без него она остаётся замороженной с кнопкой «играет».
        # _current очищаем ниже, иначе финальный снимок будет не из чего собрать
        await self._stop_now_playing(gid, final_fill=True)
        self._np_finished.pop(gid, None)
        self._current.pop(gid, None)
        self._loop_modes.pop(gid, None)
        self._skip_loop_once.discard(gid)
        self._played.pop(gid, None)
        self._cancel_alone_timer(gid)
        self._cancel_idle_timer(gid)
        self._auto_paused.discard(gid)

    # --- авто-пауза и авто-выход ------------------------------------------

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        gid = member.guild.id
        if self.bot.user is not None and member.id == self.bot.user.id:
            # бота выдернули извне -> чистим сессию; перетащили -> пересматриваем одиночество
            if after.channel is None:
                if before.channel is not None:
                    await self._handle_forced_disconnect(gid)
            else:
                await self._reevaluate_alone(gid)
            return
        vc = member.guild.voice_client
        if vc is None or vc.channel is None:
            return
        # реагируем только на изменения в канале бота
        if after.channel != vc.channel and before.channel != vc.channel:
            return
        await self._reevaluate_alone(gid)

    async def _reevaluate_alone(self, gid: int):
        """Один в канале -> авто-пауза и таймер выхода; кто-то есть -> отмена и резюме"""
        guild = self.bot.get_guild(gid)
        vc = guild.voice_client if guild is not None else None
        if vc is None or vc.channel is None:
            return
        alone = not any(not m.bot for m in vc.channel.members)
        if alone:
            if vc.is_playing():
                vc.pause()
                self._auto_paused.add(gid)
                if gid not in self._pause_start:
                    self._pause_start[gid] = time.monotonic()
                await self._refresh_now_playing(gid)
                session_log(gid, 'auto-paused (канал опустел)')
            self._start_alone_timer(gid)
        else:
            self._cancel_alone_timer(gid)
            if gid in self._auto_paused:
                self._auto_paused.discard(gid)
                if vc.is_paused():
                    vc.resume()
                    pause_start = self._pause_start.pop(gid, None)
                    if pause_start is not None:
                        self._pause_total[gid] = self._pause_total.get(gid, 0.0) + (time.monotonic() - pause_start)
                    await self._refresh_now_playing(gid)
                    session_log(gid, 'auto-resumed (кто-то вернулся)')

    def _start_alone_timer(self, gid: int):
        task = self._alone_tasks.get(gid)
        if task is not None and not task.done():
            return
        self._alone_tasks[gid] = self._spawn(self._alone_timeout(gid))

    def _cancel_alone_timer(self, gid: int):
        task = self._alone_tasks.pop(gid, None)
        if task is not None and not task.done():
            task.cancel()

    async def _alone_timeout(self, gid: int):
        try:
            await asyncio.sleep(ALONE_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            return
        self._alone_tasks.pop(gid, None)
        guild = self.bot.get_guild(gid)
        vc = guild.voice_client if guild is not None else None
        if vc is None or vc.channel is None:
            return
        if any(not m.bot for m in vc.channel.members):
            return
        await self._reset_session(gid)
        try:
            await vc.disconnect()
        except Exception:
            pass
        session_log(gid, f'auto-left (один в канале {ALONE_TIMEOUT_SECONDS // 60} мин)')
        close_guild_session(gid)

    def _start_idle_timer(self, gid: int):
        task = self._idle_tasks.get(gid)
        if task is not None and not task.done():
            return
        self._idle_tasks[gid] = self._spawn(self._idle_timeout(gid))

    def _cancel_idle_timer(self, gid: int):
        task = self._idle_tasks.pop(gid, None)
        if task is not None and not task.done():
            task.cancel()

    def _refresh_idle_timer(self, gid: int):
        """Запустить таймер простоя, если бот подключён и простаивает; иначе снять"""
        guild = self.bot.get_guild(gid)
        vc = guild.voice_client if guild is not None else None
        if vc is None or vc.channel is None:
            self._cancel_idle_timer(gid)
            return
        if vc.is_playing() or vc.is_paused() or self.get_queue(gid):
            self._cancel_idle_timer(gid)
        else:
            self._start_idle_timer(gid)

    async def _idle_timeout(self, gid: int):
        try:
            await asyncio.sleep(IDLE_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            return
        self._idle_tasks.pop(gid, None)
        guild = self.bot.get_guild(gid)
        vc = guild.voice_client if guild is not None else None
        if vc is None or vc.channel is None:
            return
        if vc.is_playing() or vc.is_paused() or self.get_queue(gid):
            return
        await self._reset_session(gid)
        try:
            await vc.disconnect()
        except Exception:
            pass
        session_log(gid, f'auto-left (простой {IDLE_TIMEOUT_SECONDS // 60} мин)')
        close_guild_session(gid)

    async def _handle_forced_disconnect(self, gid: int):
        await self._reset_session(gid)
        session_log(gid, 'voice disconnected (externally)')
        close_guild_session(gid)

    @commands.hybrid_command(description='Отключиться от голосового канала')
    async def leave(self, ctx):
        """Отключиться от голосового канала"""
        if ctx.voice_client is None:
            return await self._private(ctx, 'Я не подключена к голосовому каналу')
        await self._ensure_deferred(ctx, private=True)
        await self._reset_session(ctx.guild.id)
        await ctx.voice_client.disconnect()
        session_log(ctx.guild.id, 'left voice channel (/leave)')
        close_guild_session(ctx.guild.id)
        await self._private(ctx, 'Отключилась')

    # --- очередь ----------------------------------------------------------

    @commands.hybrid_command(description='Очистить очередь')
    async def clear(self, ctx):
        """Очистить очередь, текущий трек продолжает играть"""
        await self._ensure_deferred(ctx, private=True)
        gid = ctx.guild.id
        removed = len(self.get_queue(gid))
        self.get_queue(gid).clear()
        self._drop_prefetched(gid)
        self._played.pop(gid, None)
        self._refresh_idle_timer(gid)
        session_log(gid, f'queue cleared (/clear, {removed} tracks)')
        await self._private(ctx, f'Очередь очищена ({removed} треков). Текущий трек доиграет')

    async def _enqueue(self, ctx, url: str, *, shuffle: bool, front: bool = False):
        # публично: слот заглушки занимает карточка добавления
        await self._ensure_deferred(ctx)
        try:
            kind, payload = await extract(url, loop=self.bot.loop)
        except asyncio.TimeoutError:
            session_log(ctx.guild.id, f'enqueue timeout: {url!r}')
            return await self._transient(ctx, 'Таймаут при загрузке. Попробуй ещё раз')
        except youtube_dl.utils.DownloadError as exc:
            session_log(ctx.guild.id, f'enqueue download error: {url!r}: {exc!r}')
            return await self._transient(ctx, f'Не удалось загрузить: {plain_error(exc)}')
        except Exception as exc:
            print(f'enqueue error: {exc!r}')
            session_log(ctx.guild.id, f'enqueue error: {url!r}: {exc!r}')
            return await self._transient(ctx, f'Ошибка: {plain_error(exc)}')

        # extract идёт до 30 с, за это время бота могли выдернуть из канала
        vc = ctx.voice_client
        if vc is None:
            return await self._transient(ctx, 'Я больше не в голосовом канале — добавлять некуда')

        queue = self.get_queue(ctx.guild.id)
        was_playing = vc.is_playing() or vc.is_paused()

        if kind == 'playlist':
            if shuffle:
                random.shuffle(payload.tracks)
            if front:
                # extendleft разворачивает порядок, поэтому подаём задом наперёд
                queue.extendleft(reversed(payload.tracks))
            else:
                queue.extend(payload.tracks)
            session_log(
                ctx.guild.id,
                f'queued {payload.kind}: {payload.title!r} ({len(payload.tracks)} tracks'
                f'{", shuffled" if shuffle else ""}{", next" if front else ""})',
            )
            await self._added_card(ctx, build_added_playlist_embed(
                payload, shuffled=shuffle, next_up=front))
        else:
            if front:
                queue.appendleft(payload)
            else:
                queue.append(payload)
            session_log(ctx.guild.id, f'queued track{" (next)" if front else ""}: '
                                      f'{format_track_label(payload)}')
            await self._added_card(ctx, build_added_track_embed(payload, next_up=front))

        self._resync_prefetch(ctx.guild.id, vc)
        await self._start_if_idle(ctx)

    @commands.hybrid_command(description='Воспроизвести трек или плейлист')
    @app_commands.describe(
        url='Ссылка на трек/плейлист или поисковый запрос',
        shuffle='Перемешать плейлист при добавлении (для одного трека игнорируется)',
    )
    async def play(self, ctx, *, url: str, shuffle: bool = False):
        """Воспроизвести трек или плейлист"""
        await self._enqueue(ctx, url, shuffle=shuffle)

    @commands.hybrid_command(description='Поставить трек или плейлист сразу после текущего')
    @app_commands.describe(
        url='Ссылка на трек/плейлист или поисковый запрос',
        shuffle='Перемешать плейлист перед вставкой (для одного трека игнорируется)',
    )
    async def playnext(self, ctx, *, url: str, shuffle: bool = False):
        """Поставить в начало очереди: заиграет сразу после текущего трека"""
        await self._enqueue(ctx, url, shuffle=shuffle, front=True)

    @commands.hybrid_command(description='Перемешать текущую очередь')
    async def shuffle(self, ctx):
        """Перемешать текущую очередь"""
        await self._ensure_deferred(ctx, private=True)
        gid = ctx.guild.id
        queue = self.get_queue(gid)
        if not queue:
            return await self._private(ctx, 'Очередь пуста — нечего перемешивать')
        items = list(queue)
        random.shuffle(items)
        queue.clear()
        queue.extend(items)
        # предзагруженный трек больше не первый в очереди
        self._resync_prefetch(gid, ctx.voice_client)
        session_log(gid, f'queue shuffled ({len(items)} tracks)')
        await self._private(ctx, f'Очередь перемешана ({len(items)} треков)')

    # --- управление воспроизведением --------------------------------------

    @commands.hybrid_command(description='Поставить трек на паузу')
    async def pause(self, ctx):
        """Поставить трек на паузу"""
        if ctx.voice_client is None or not ctx.voice_client.is_playing():
            return await self._private(ctx, 'Сейчас ничего не играет')
        ctx.voice_client.pause()
        gid = ctx.guild.id
        if gid not in self._pause_start:
            self._pause_start[gid] = time.monotonic()
        await self._refresh_now_playing(gid)
        session_log(gid, 'paused')
        await self._private(ctx, 'Пауза')

    @commands.hybrid_command(description='Продолжить воспроизведение после паузы')
    async def resume(self, ctx):
        """Продолжить воспроизведение после паузы"""
        if ctx.voice_client is None or not ctx.voice_client.is_paused():
            return await self._private(ctx, 'Сейчас не на паузе')
        ctx.voice_client.resume()
        gid = ctx.guild.id
        self._auto_paused.discard(gid)
        pause_start = self._pause_start.pop(gid, None)
        if pause_start is not None:
            self._pause_total[gid] = self._pause_total.get(gid, 0.0) + (time.monotonic() - pause_start)
        await self._refresh_now_playing(gid)
        session_log(gid, 'resumed')
        await self._private(ctx, 'Продолжаю')

    def _active_voice(self, ctx):
        """VoiceClient, если играет или на паузе, иначе None

        Пауза считается активным состоянием: vc.stop() на приостановленном плеере работает
        """
        vc = ctx.voice_client
        if vc is None or not (vc.is_playing() or vc.is_paused()):
            return None
        return vc

    def _drop_from_queue(self, gid: int, count: int) -> int:
        """Снять count треков с головы очереди, в режиме repeat queue сохранить их в _played"""
        queue = self.get_queue(gid)
        to_drop = min(count, len(queue))
        dropped = [queue.popleft() for _ in range(to_drop)]
        if dropped and self._loop_modes.get(gid) == 'queue':
            self._played.setdefault(gid, []).extend(dropped)
        return to_drop

    @commands.hybrid_command(description='Пропустить треки')
    @app_commands.describe(count='Сколько треков пропустить (по умолчанию 1)')
    async def skip(self, ctx, count: int = 1):
        """Пропустить треки"""
        vc = self._active_voice(ctx)
        if vc is None:
            return await self._private(ctx, 'Сейчас ничего не играет')
        if count < 1:
            return await self._private(ctx, 'Количество должно быть больше 0')
        gid = ctx.guild.id
        to_drop = self._drop_from_queue(gid, count - 1)
        if to_drop > 0:
            self._drop_prefetched(gid)
        if self._loop_modes.get(gid) == 'track':
            self._skip_loop_once.add(gid)
        vc.stop()
        total = 1 + to_drop
        session_log(gid, f'skipped {total} track(s)')
        await self._private(ctx, f'Пропущено треков: {total}' if total > 1 else 'Трек пропущен')

    @commands.hybrid_command(description='Перейти к треку в очереди по номеру')
    @app_commands.describe(position='Номер трека в очереди (начиная с 1)')
    async def skipto(self, ctx, position: int):
        """Перейти к треку в очереди по номеру"""
        vc = self._active_voice(ctx)
        if vc is None:
            return await self._private(ctx, 'Сейчас ничего не играет')
        if position < 1:
            return await self._private(ctx, 'Номер должен быть больше 0')
        gid = ctx.guild.id
        queue = self.get_queue(gid)
        if not queue:
            return await self._private(ctx, 'Очередь пуста')
        if position > len(queue):
            return await self._private(ctx, f'В очереди только {len(queue)} треков')
        self._drop_from_queue(gid, position - 1)
        self._drop_prefetched(gid)
        if self._loop_modes.get(gid) == 'track':
            self._skip_loop_once.add(gid)
        target = queue[0]
        vc.stop()
        session_log(gid, f'skipto #{position}: {format_track_label(target)}')
        await self._private(ctx, f'Перехожу к #{position}: {format_track_label(target)}')

    @commands.hybrid_command(description='Управлять режимом повтора')
    @app_commands.describe(mode='off — без повтора, track — повторять трек, queue — повторять очередь')
    @app_commands.choices(mode=[
        app_commands.Choice(name='off', value='off'),
        app_commands.Choice(name='track', value='track'),
        app_commands.Choice(name='queue', value='queue'),
    ])
    async def repeat(self, ctx, mode: app_commands.Choice[str]):
        """Управлять режимом повтора"""
        # текстовая форма гибридной команды отдаёт строку, слэш — Choice
        value = mode.value if hasattr(mode, 'value') else str(mode)
        if value not in ('off', 'track', 'queue'):
            return await self._private(ctx, 'Доступные режимы: off, track, queue')
        gid = ctx.guild.id
        prev = self._loop_modes.get(gid, 'off')
        if prev == 'queue' and value != 'queue':
            self._played.pop(gid, None)
        if value == 'off':
            self._loop_modes.pop(gid, None)
            self._skip_loop_once.discard(gid)
            await self._private(ctx, 'Повтор выключен')
            await self._refresh_now_playing(gid)
        elif value == 'track':
            self._loop_modes[gid] = value
            await self._private(ctx, 'Повтор: текущий трек')
            await self._refresh_now_playing(gid)
        else:
            self._loop_modes[gid] = value
            await self._private(ctx, 'Повтор: очередь')
            await self._refresh_now_playing(gid)
        session_log(gid, f'repeat mode: {value}')

    @commands.hybrid_command(name='queue', description='Показать очередь')
    async def queue_cmd(self, ctx):
        """Показать очередь"""
        gid = ctx.guild.id
        if not self.get_queue(gid):
            return await self._private(ctx, 'Очередь пуста')
        view = QueueView(lambda: self.get_queue(gid), owner_id=ctx.author.id)
        msg = await self._private(ctx, embed=view.build_embed(), view=view)
        view.message = msg

    @commands.hybrid_command(description='Остановить воспроизведение и очистить очередь (без выхода)')
    async def stop(self, ctx):
        """Остановить воспроизведение и очистить очередь, остаться в канале"""
        await self._ensure_deferred(ctx, private=True)
        gid = ctx.guild.id
        if ctx.voice_client is None:
            return await self._private(ctx, 'Я не подключена к голосовому каналу')
        was_active = ctx.voice_client.is_playing() or ctx.voice_client.is_paused()
        await self._reset_session(gid)
        if was_active:
            ctx.voice_client.stop()
        self._refresh_idle_timer(gid)
        session_log(gid, 'stopped (queue cleared, stayed in channel)')
        await self._private(ctx, 'Остановлена. Очередь очищена')

    @commands.hybrid_command(name='nowplaying', aliases=['np'], description='Показать текущий трек')
    async def nowplaying(self, ctx):
        """Снапшот текущего трека без прогресс-бара"""
        gid = ctx.guild.id
        track = self._current.get(gid)
        source = self._np_source.get(gid)
        if track is None or source is None or self._active_voice(ctx) is None:
            return await self._private(ctx, 'Сейчас ничего не играет')
        await self._private(ctx, embed=build_current_track_embed(track, source))

    # --- перемотка --------------------------------------------------------

    async def _apply_seek(self, ctx, target_pos: float, *, log_label: str) -> str:
        """Сдвинуть текущий трек на target_pos абсолютных секунд, вернуть текст ответа"""
        vc = ctx.voice_client
        gid = ctx.guild.id
        source = self._np_source.get(gid)
        track = self._current.get(gid)
        if source is None or track is None:
            return 'Не могу перемотать: нет активного источника'
        duration = float(source.data.get('duration') or 0)
        new_pos = max(0.0, target_pos)
        if duration > 0 and new_pos >= duration - END_OF_TRACK_EPSILON_SECONDS:
            if self._loop_modes.get(gid) == 'track':
                self._skip_loop_once.add(gid)
            vc.stop()
            session_log(gid, f'{log_label} past end -> skip')
            return 'Трек пропущен (перемотка за конец)'

        was_paused = vc.is_paused()
        try:
            new_source = YTDLSource.from_resolved(source.data, start=new_pos)
        except Exception as exc:
            print(f'{log_label} error: {exc!r}')
            session_log(gid, f'{log_label} error: {exc!r}')
            return f'Ошибка перемотки: {plain_error(exc)}'

        # set_source() не чистит старый источник, иначе поток буферизации остаётся висеть;
        # cleanup откладываем: если аудио-поток залип в old.read(), немедленный cleanup
        # вернул бы b'' и плеер счёл бы это концом трека
        vc.source = new_source
        # set_source() внутри делает pause() -> подмена -> resume() безусловно
        if was_paused and not vc.is_paused():
            vc.pause()
        self._schedule_source_cleanup(source)

        self._np_source[gid] = new_source
        now = time.monotonic()
        self._track_start[gid] = now - new_pos
        self._pause_total[gid] = 0.0
        if was_paused:
            self._pause_start[gid] = now
        else:
            self._pause_start.pop(gid, None)

        session_log(gid, f'{log_label} -> {new_pos:.0f}s')
        await self._refresh_now_playing(gid)
        return f'Текущая позиция: {format_time(new_pos)}'

    @commands.hybrid_command(description='Перемотать на N секунд (отрицательное — назад)')
    @app_commands.describe(seconds='Сдвиг в секундах (положительное — вперёд, отрицательное — назад)')
    async def seek(self, ctx, seconds: int):
        """Перемотать текущий трек на N секунд"""
        if self._active_voice(ctx) is None:
            return await self._private(ctx, 'Сейчас ничего не играет')
        if seconds == 0:
            return await self._private(ctx, 'Сдвиг должен быть отличен от 0')
        target = self._elapsed(ctx.guild.id) + seconds
        reply = await self._apply_seek(ctx, target, log_label=f'seek {seconds:+d}s')
        await self._private(ctx, reply)

    @commands.hybrid_command(description='Перейти к конкретной позиции трека (секунды или mm:ss)')
    @app_commands.describe(position='Позиция: число секунд, либо mm:ss / h:mm:ss')
    async def seekto(self, ctx, position: str):
        """Перейти к абсолютной позиции в треке"""
        if self._active_voice(ctx) is None:
            return await self._private(ctx, 'Сейчас ничего не играет')
        target = _parse_time_position(position)
        if target is None or target < 0:
            return await self._private(ctx, 'Позиция должна быть числом секунд или строкой mm:ss / h:mm:ss')
        reply = await self._apply_seek(ctx, target, log_label=f'seekto {position!r}')
        await self._private(ctx, reply)

    # --- хуки -------------------------------------------------------------

    async def _ensure_voice(self, ctx):
        # Публично: слот заглушки займёт карточка добавления, а она публичная.
        # Личный дефёр делает личной и её — эфемерность задаётся дефёром,
        # и ответ её уже не переопределит
        await self._ensure_deferred(ctx)
        if ctx.voice_client is None:
            if ctx.author.voice:
                channel = ctx.author.voice.channel
                await channel.connect(self_deaf=True)
                open_guild_session(ctx.guild.id, ctx.guild.name)
                session_log(ctx.guild.id, f'auto-joined voice channel: {channel.name}')
            else:
                # дефёр здесь публичный, эфемерным ответ уже не сделать —
                # поэтому убираем его по таймеру
                await self._transient(ctx, 'Вы не подключены к голосовому каналу')
                raise commands.CommandError('Author not connected to a voice channel.')

    @play.before_invoke
    async def _play_ensure_voice(self, ctx):
        await self._ensure_voice(ctx)

    @playnext.before_invoke
    async def _playnext_ensure_voice(self, ctx):
        # playnext добавляет так же, как play, значит и подключаться должен так же
        await self._ensure_voice(ctx)


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
