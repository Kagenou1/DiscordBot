import asyncio
import logging
import random
import threading
import time
from collections import deque

import discord
import yt_dlp as youtube_dl
from discord import app_commands
from discord.ext import commands

from audio import Track, YTDLSource, extract
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
)


_log = logging.getLogger('audio').info


ALLOWED_REMAINING_LENGTH = 1


def _parse_time_position(text: str) -> float | None:
    """Разобрать '90', '1:30', '1:02:03' в секунды. None при невалидном вводе."""
    parts = text.strip().split(':')
    try:
        if len(parts) == 1:
            return float(parts[0])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError:
        return None
    return None


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queues: dict[int, deque[Track]] = {}
        self._prefetched: dict[int, tuple[Track, YTDLSource]] = {}
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

    def get_queue(self, guild_id: int) -> deque[Track]:
        return self.queues.setdefault(guild_id, deque())

    async def _ensure_deferred(self, ctx):
        if ctx.interaction is not None and not ctx.interaction.response.is_done():
            await ctx.defer()

    def _drop_prefetched(self, guild_id: int):
        prefetched = self._prefetched.pop(guild_id, None)
        if prefetched:
            try:
                prefetched[1].cleanup()
            except Exception:
                pass

    @staticmethod
    def _schedule_source_cleanup(source, delay: float = 2.0):
        """Погасить источник (ffmpeg + поток буферизации) спустя delay секунд."""
        def _run():
            time.sleep(delay)
            try:
                source.cleanup()
            except Exception:
                pass
        threading.Thread(target=_run, name='src-cleanup', daemon=True).start()

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

    async def _refresh_now_playing(self, gid: int):
        msg = self._np_msg.get(gid)
        source = self._np_source.get(gid)
        track = self._current.get(gid)
        if msg is None or source is None or track is None:
            return
        embed = build_now_playing_embed(
            track, source, self._elapsed(gid), state=self._playback_state(gid),
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
                    if duration - elapsed <= ALLOWED_REMAINING_LENGTH:
                        elapsed = duration
                    else:
                        elapsed = min(elapsed, duration)
                embed = build_now_playing_embed(track, source, elapsed, state='stopped')
                try:
                    await msg.edit(embed=embed)
                except discord.HTTPException:
                    pass
        self._track_start.pop(gid, None)
        self._pause_start.pop(gid, None)
        self._pause_total.pop(gid, None)

    def _after_play(self, ctx, error):
        if error:
            print(f'Player error: {error}')
            session_log(ctx.guild.id, f'player error: {error!r}')
        fut = asyncio.run_coroutine_threadsafe(self._play_next(ctx), self.bot.loop)
        try:
            fut.result()
        except Exception as exc:
            print(f'play_next error: {exc}')
            session_log(ctx.guild.id, f'play_next error: {exc!r}')

    async def _play_next(self, ctx):
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
                current._resolved = None
                current._resolved_at = 0.0
                self._played.setdefault(gid, []).append(current)
            if not queue and self._played.get(gid):
                queue.extend(self._played[gid])
                self._played[gid].clear()

        t_total = time.perf_counter()
        chosen: tuple[Track, YTDLSource] | None = None
        used_prefetched = False

        prefetched = self._prefetched.pop(gid, None)
        if prefetched and queue and queue[0] is prefetched[0]:
            queue.popleft()
            chosen = prefetched
            used_prefetched = True
        elif prefetched:
            try:
                prefetched[1].cleanup()
            except Exception:
                pass

        while chosen is None and queue:
            track = queue.popleft()
            try:
                source = await track.make_source(loop=self.bot.loop)
                chosen = (track, source)
            except Exception as exc:
                print(f'failed to load {track.title!r}: {exc!r}')
                session_log(gid, f'failed to load {track.title!r}: {exc!r}')
                await ctx.send(f'Пропускаю «{track.title}» (ошибка загрузки).')

        if chosen is None:
            self._current.pop(gid, None)
            return

        track, source = chosen
        self._current[gid] = track
        ctx.voice_client.play(source, after=lambda e: self._after_play(ctx, e))
        _log(f'play_next -> playing {track.title!r} in {(time.perf_counter() - t_total) * 1000:.0f} ms (prefetched={used_prefetched})')
        session_log(gid, f'playing: {format_track_label(track)} (prefetched={used_prefetched})')

        self._track_start[gid] = time.monotonic()
        self._pause_total[gid] = 0.0
        self._pause_start.pop(gid, None)
        msg = await ctx.send(embed=build_now_playing_embed(track, source, 0.0, state='playing'))
        self._np_msg[gid] = msg
        self._np_source[gid] = source
        self._np_task[gid] = asyncio.create_task(self._now_playing_updater(gid, source, track))

        if loop_mode != 'track':
            asyncio.create_task(self._prefetch(gid))

    async def _prefetch(self, guild_id: int):
        if guild_id in self._prefetched:
            return
        queue = self.get_queue(guild_id)
        if not queue:
            return
        track = queue[0]
        t0 = time.perf_counter()
        try:
            source = await track.make_source(loop=self.bot.loop)
        except Exception as exc:
            print(f'prefetch error for {track.title!r}: {exc!r}')
            return
        cur_queue = self.get_queue(guild_id)
        if cur_queue and cur_queue[0] is track and guild_id not in self._prefetched:
            self._prefetched[guild_id] = (track, source)
            _log(f'prefetched {track.title!r} in {(time.perf_counter() - t0) * 1000:.0f} ms')
        else:
            try:
                source.cleanup()
            except Exception:
                pass

    @commands.hybrid_command(description='Подключиться к голосовому каналу')
    @app_commands.describe(channel='Канал для подключения (необязательно)')
    async def join(self, ctx, *, channel: discord.VoiceChannel = None):
        """Подключиться к голосовому каналу."""
        await self._ensure_deferred(ctx)
        if channel is None:
            if ctx.author.voice is None:
                return await ctx.send('Вы не в голосовом канале.')
            channel = ctx.author.voice.channel
        if ctx.voice_client is not None:
            await ctx.voice_client.move_to(channel)
            session_log(ctx.guild.id, f'moved to voice channel: {channel.name}')
        else:
            await channel.connect(self_deaf=True)
            open_guild_session(ctx.guild.id, ctx.guild.name)
            session_log(ctx.guild.id, f'joined voice channel: {channel.name}')
        await ctx.send(f'Подключился к {channel.name}.')

    async def _reset_session(self, gid: int):
        self.get_queue(gid).clear()
        self._drop_prefetched(gid)
        await self._stop_now_playing(gid)
        self._current.pop(gid, None)
        self._loop_modes.pop(gid, None)
        self._skip_loop_once.discard(gid)
        self._played.pop(gid, None)

    @commands.hybrid_command(description='Отключиться от голосового канала')
    async def leave(self, ctx):
        """Отключиться от голосового канала."""
        if ctx.voice_client is None:
            return await ctx.send('Я не подключён к голосовому каналу.')
        await self._ensure_deferred(ctx)
        await self._reset_session(ctx.guild.id)
        await ctx.voice_client.disconnect()
        session_log(ctx.guild.id, 'left voice channel (/leave)')
        close_guild_session(ctx.guild.id)
        await ctx.send('Отключился.')

    @commands.hybrid_command(description='Очистить очередь')
    async def clear(self, ctx):
        """Очистить очередь."""
        await self._reset_session(ctx.guild.id)
        session_log(ctx.guild.id, 'queue cleared (/clear)')
        await ctx.send('Очередь очищена.')

    async def _enqueue(self, ctx, url: str, *, shuffle: bool):
        await self._ensure_deferred(ctx)
        try:
            kind, payload = await extract(url, loop=self.bot.loop)
        except asyncio.TimeoutError:
            session_log(ctx.guild.id, f'enqueue timeout: {url!r}')
            return await ctx.send('Таймаут при загрузке. Попробуй ещё раз.')
        except youtube_dl.utils.DownloadError as exc:
            session_log(ctx.guild.id, f'enqueue download error: {url!r}: {exc!r}')
            return await ctx.send(f'Не удалось загрузить: {exc}')
        except Exception as exc:
            print(f'enqueue error: {exc!r}')
            session_log(ctx.guild.id, f'enqueue error: {url!r}: {exc!r}')
            return await ctx.send(f'Ошибка: {exc}')

        queue = self.get_queue(ctx.guild.id)
        was_playing = ctx.voice_client.is_playing() or ctx.voice_client.is_paused()

        if kind == 'playlist':
            if shuffle:
                random.shuffle(payload.tracks)
            queue.extend(payload.tracks)
            session_log(
                ctx.guild.id,
                f'queued {payload.kind}: {payload.title!r} ({len(payload.tracks)} tracks'
                f'{", shuffled" if shuffle else ""})',
            )
            await ctx.send(embed=build_added_playlist_embed(payload, shuffled=shuffle))
        else:
            queue.append(payload)
            session_log(ctx.guild.id, f'queued track: {format_track_label(payload)}')
            if was_playing:
                await ctx.send(embed=build_added_track_embed(payload))

        if not was_playing:
            await self._play_next(ctx)

    @commands.hybrid_command(description='Воспроизвести трек или плейлист')
    @app_commands.describe(
        url='Ссылка на трек/плейлист или поисковый запрос',
        shuffle='Перемешать плейлист при добавлении (для одного трека игнорируется)',
    )
    async def play(self, ctx, *, url: str, shuffle: bool = False):
        """Воспроизвести трек или плейлист."""
        await self._enqueue(ctx, url, shuffle=shuffle)

    @commands.hybrid_command(description='Перемешать текущую очередь')
    async def shuffle(self, ctx):
        """Перемешать текущую очередь."""
        await self._ensure_deferred(ctx)
        gid = ctx.guild.id
        queue = self.get_queue(gid)
        if not queue:
            return await ctx.send('Очередь пуста — нечего перемешивать.')
        items = list(queue)
        random.shuffle(items)
        queue.clear()
        queue.extend(items)
        self._drop_prefetched(gid)
        if ctx.voice_client is not None and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused()):
            asyncio.create_task(self._prefetch(gid))
        session_log(gid, f'queue shuffled ({len(items)} tracks)')
        await ctx.send(f'Очередь перемешана ({len(items)} треков).')

    @commands.hybrid_command(description='Поставить трек на паузу')
    async def pause(self, ctx):
        """Поставить трек на паузу."""
        if ctx.voice_client is None or not ctx.voice_client.is_playing():
            return await ctx.send('Сейчас ничего не играет.')
        ctx.voice_client.pause()
        gid = ctx.guild.id
        if gid not in self._pause_start:
            self._pause_start[gid] = time.monotonic()
        await self._refresh_now_playing(gid)
        session_log(gid, 'paused')
        await ctx.send('Пауза.')

    @commands.hybrid_command(description='Продолжить воспроизведение после паузы')
    async def resume(self, ctx):
        """Продолжить воспроизведение после паузы."""
        if ctx.voice_client is None or not ctx.voice_client.is_paused():
            return await ctx.send('Сейчас не на паузе.')
        ctx.voice_client.resume()
        gid = ctx.guild.id
        pause_start = self._pause_start.pop(gid, None)
        if pause_start is not None:
            self._pause_total[gid] = self._pause_total.get(gid, 0.0) + (time.monotonic() - pause_start)
        await self._refresh_now_playing(gid)
        session_log(gid, 'resumed')
        await ctx.send('Продолжаю.')

    @commands.hybrid_command(description='Пропустить треки')
    @app_commands.describe(count='Сколько треков пропустить (по умолчанию 1)')
    async def skip(self, ctx, count: int = 1):
        """Пропустить треки."""
        if ctx.voice_client is None or not ctx.voice_client.is_playing():
            return await ctx.send('Сейчас ничего не играет.')
        if count < 1:
            return await ctx.send('Количество должно быть больше 0.')
        gid = ctx.guild.id
        queue = self.get_queue(gid)
        to_drop = min(count - 1, len(queue))
        for _ in range(to_drop):
            queue.popleft()
        if to_drop > 0:
            self._drop_prefetched(gid)
        if self._loop_modes.get(gid) == 'track':
            self._skip_loop_once.add(gid)
        ctx.voice_client.stop()
        total = 1 + to_drop
        session_log(gid, f'skipped {total} track(s)')
        await ctx.send(f'Пропущено треков: {total}.' if total > 1 else 'Трек пропущен.')

    @commands.hybrid_command(description='Перейти к треку в очереди по номеру')
    @app_commands.describe(position='Номер трека в очереди (начиная с 1)')
    async def skipto(self, ctx, position: int):
        """Перейти к треку в очереди по номеру."""
        if ctx.voice_client is None or not ctx.voice_client.is_playing():
            return await ctx.send('Сейчас ничего не играет.')
        if position < 1:
            return await ctx.send('Номер должен быть больше 0.')
        gid = ctx.guild.id
        queue = self.get_queue(gid)
        if not queue:
            return await ctx.send('Очередь пуста.')
        if position > len(queue):
            return await ctx.send(f'В очереди только {len(queue)} треков.')
        for _ in range(position - 1):
            queue.popleft()
        self._drop_prefetched(gid)
        if self._loop_modes.get(gid) == 'track':
            self._skip_loop_once.add(gid)
        target = queue[0]
        ctx.voice_client.stop()
        session_log(gid, f'skipto #{position}: {format_track_label(target)}')
        await ctx.send(f'Перехожу к #{position}: {format_track_label(target)}.')

    @commands.hybrid_command(description='Управлять режимом повтора')
    @app_commands.describe(mode='off — без повтора, track — повторять трек, queue — повторять очередь')
    @app_commands.choices(mode=[
        app_commands.Choice(name='off', value='off'),
        app_commands.Choice(name='track', value='track'),
        app_commands.Choice(name='queue', value='queue'),
    ])
    async def repeat(self, ctx, mode: app_commands.Choice[str]):
        """Управлять режимом повтора."""
        value = mode.value if hasattr(mode, 'value') else str(mode)
        if value not in ('off', 'track', 'queue'):
            return await ctx.send('Доступные режимы: off, track, queue.')
        gid = ctx.guild.id
        prev = self._loop_modes.get(gid, 'off')
        if prev == 'queue' and value != 'queue':
            self._played.pop(gid, None)
        if value == 'off':
            self._loop_modes.pop(gid, None)
            self._skip_loop_once.discard(gid)
            await ctx.send('Повтор выключен.')
        elif value == 'track':
            self._loop_modes[gid] = value
            await ctx.send('Повтор: текущий трек.')
        else:
            self._loop_modes[gid] = value
            await ctx.send('Повтор: очередь.')
        session_log(gid, f'repeat mode: {value}')

    @commands.hybrid_command(name='queue', description='Показать очередь')
    async def queue_cmd(self, ctx):
        """Показать очередь."""
        gid = ctx.guild.id
        if not self.get_queue(gid):
            return await ctx.send('Очередь пуста.')
        view = QueueView(lambda: self.get_queue(gid))
        msg = await ctx.send(embed=view.build_embed(), view=view)
        view.message = msg

    @commands.hybrid_command(description='Остановить воспроизведение и очистить очередь (без выхода)')
    async def stop(self, ctx):
        """Остановить воспроизведение и очистить очередь, но остаться в канале."""
        await self._ensure_deferred(ctx)
        gid = ctx.guild.id
        if ctx.voice_client is None:
            return await ctx.send('Я не подключён к голосовому каналу.')
        was_active = ctx.voice_client.is_playing() or ctx.voice_client.is_paused()
        await self._reset_session(gid)
        if was_active:
            ctx.voice_client.stop()
        session_log(gid, 'stopped (queue cleared, stayed in channel)')
        await ctx.send('Остановлен. Очередь очищена.')

    @commands.hybrid_command(name='nowplaying', aliases=['np'], description='Показать текущий трек')
    async def nowplaying(self, ctx):
        """Статический снапшот текущего трека (без прогресс-бара)."""
        gid = ctx.guild.id
        track = self._current.get(gid)
        source = self._np_source.get(gid)
        if track is None or source is None or ctx.voice_client is None or not (
            ctx.voice_client.is_playing() or ctx.voice_client.is_paused()
        ):
            return await ctx.send('Сейчас ничего не играет.')
        await ctx.send(embed=build_current_track_embed(track, source))

    async def _apply_seek(self, ctx, target_pos: float, *, log_label: str) -> str:
        """Сдвинуть текущий трек на target_pos (абсолютные секунды). Возвращает сообщение для пользователя."""
        vc = ctx.voice_client
        gid = ctx.guild.id
        source = self._np_source.get(gid)
        track = self._current.get(gid)
        if source is None or track is None:
            return 'Не могу перемотать: нет активного источника.'
        duration = float(source.data.get('duration') or 0)
        new_pos = max(0.0, target_pos)
        if duration > 0 and new_pos >= duration - ALLOWED_REMAINING_LENGTH:
            if self._loop_modes.get(gid) == 'track':
                self._skip_loop_once.add(gid)
            vc.stop()
            session_log(gid, f'{log_label} past end -> skip')
            return 'Трек пропущен (перемотка за конец).'

        was_paused = vc.is_paused()
        try:
            new_source = YTDLSource.from_resolved(source.data, start=new_pos)
        except Exception as exc:
            print(f'{log_label} error: {exc!r}')
            session_log(gid, f'{log_label} error: {exc!r}')
            return f'Ошибка перемотки: {exc}'

        # set_source() в discord.py НЕ чистит старый источник — делаем это сами,
        # иначе фоновый поток буферизации старого OpusAudioSource останется висеть.
        # Очистку откладываем: если аудио-поток прямо сейчас залип в old.read()
        # (недобор ровно в момент перемотки), немедленный cleanup вернул бы b''
        # и плеер счёл бы это концом трека. За пару секунд поток уйдёт на new_source.
        vc.source = new_source
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
        return f'Текущая позиция: {format_time(new_pos)}.'

    @commands.hybrid_command(description='Перемотать на N секунд (отрицательное — назад)')
    @app_commands.describe(seconds='Сдвиг в секундах (положительное — вперёд, отрицательное — назад)')
    async def seek(self, ctx, seconds: int):
        """Перемотать текущий трек на N секунд."""
        vc = ctx.voice_client
        if vc is None or not (vc.is_playing() or vc.is_paused()):
            return await ctx.send('Сейчас ничего не играет.')
        if seconds == 0:
            return await ctx.send('Сдвиг должен быть отличен от 0.')
        target = self._elapsed(ctx.guild.id) + seconds
        reply = await self._apply_seek(ctx, target, log_label=f'seek {seconds:+d}s')
        await ctx.send(reply)

    @commands.hybrid_command(description='Перейти к конкретной позиции трека (секунды или mm:ss)')
    @app_commands.describe(position='Позиция: число секунд, либо mm:ss / h:mm:ss')
    async def seekto(self, ctx, position: str):
        """Перейти к абсолютной позиции в треке."""
        vc = ctx.voice_client
        if vc is None or not (vc.is_playing() or vc.is_paused()):
            return await ctx.send('Сейчас ничего не играет.')
        target = _parse_time_position(position)
        if target is None or target < 0:
            return await ctx.send('Позиция должна быть числом секунд или строкой mm:ss / h:mm:ss.')
        reply = await self._apply_seek(ctx, target, log_label=f'seekto {position!r}')
        await ctx.send(reply)

    async def _ensure_voice(self, ctx):
        await self._ensure_deferred(ctx)
        if ctx.voice_client is None:
            if ctx.author.voice:
                channel = ctx.author.voice.channel
                await channel.connect(self_deaf=True)
                open_guild_session(ctx.guild.id, ctx.guild.name)
                session_log(ctx.guild.id, f'auto-joined voice channel: {channel.name}')
            else:
                await ctx.send('Вы не подключены к голосовому каналу.')
                raise commands.CommandError('Author not connected to a voice channel.')

    @play.before_invoke
    async def _play_ensure_voice(self, ctx):
        await self._ensure_voice(ctx)


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
