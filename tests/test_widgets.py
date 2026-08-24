"""Виджеты: форматтеры, эмбеды, пагинация очереди"""
import pytest

from conftest import track
from widgets import format_time, format_track_label, progress_bar, source_label
from widgets.added.embed import build_added_playlist_embed, build_added_track_embed
from widgets.now_playing.embed import (build_current_track_embed,
                                       build_now_playing_embed)
from widgets.now_playing.thumbnail import pick_thumbnail
from widgets.queue.embed import _DESCRIPTION_LIMIT, build_queue_embed
from widgets.queue.view import QueueView

from audio.track import PlaylistInfo


@pytest.mark.parametrize('secs,expected', [
    (0, '0:00'), (5, '0:05'), (65, '1:05'), (600, '10:00'),
    (3600, '1:00:00'), (3725, '1:02:05'), (-10, '0:00'),
])
def test_format_time(secs, expected):
    assert format_time(secs) == expected


def test_format_track_label():
    assert format_track_label(track(title='S', artist='A')) == 'S — A'
    assert format_track_label(track(title='S', artist='')) == 'S'


def test_progress_bar_unknown_duration():
    bar = progress_bar(30, 0)
    assert '▰' not in bar and '0:30' in bar


def test_progress_bar_clamps_overrun():
    bar = progress_bar(500, 100)
    assert '1:40 / 1:40' in bar
    assert '▱' not in bar


def test_progress_bar_midpoint():
    bar = progress_bar(50, 100, width=10)
    assert bar.count('▰') == 5 and bar.count('▱') == 5


@pytest.mark.parametrize('url,label', [
    ('https://music.youtube.com/watch?v=x', 'YouTube Music'),
    ('https://www.youtube.com/watch?v=x', 'YouTube'),
    ('https://youtu.be/x', 'YouTube'),
    ('https://open.spotify.com/track/x', 'Spotify'),
    ('https://music.yandex.ru/track/1', 'Yandex Music'),
    ('https://soundcloud.com/u/t', 'SoundCloud'),
    ('https://vk.ru/audio-1_2_abc', 'VK Музыка'),
    ('https://vk.com/audio-1_2', 'VK Музыка'),
    ('https://vk.ru/music/playlist/-1_2', 'VK Музыка'),
    ('https://vk.ru/music/album/-1_2', 'VK Музыка'),
    # профили и видео VK музыкой не являются
    ('https://vk.com/durov', ''),
    ('https://vk.com/video-1_2', ''),
    ('https://example.com', ''),
    ('', ''),
])
def test_source_label(url, label):
    assert source_label(url) == label


def test_pick_thumbnail_prefers_direct_field():
    assert pick_thumbnail({'thumbnail': 'a', 'thumbnails': [{'url': 'b'}]}) == 'a'
    assert pick_thumbnail({'thumbnails': [{'url': 'b'}, {'url': 'c'}]}) == 'c'
    assert pick_thumbnail({}) is None


# --- эмбед очереди ----------------------------------------------------------

def test_queue_embed_empty():
    assert build_queue_embed([], page=0, page_size=20, max_page=0).title == 'Очередь пуста'


def test_queue_embed_numbers_continue_across_pages():
    items = [track(title=f'S{i}', url=f'u{i}') for i in range(45)]
    embed = build_queue_embed(items, page=2, page_size=20, max_page=2)
    assert '`41.`' in embed.description
    assert 'Страница 3 / 3' in embed.footer.text
    assert 'Очередь — 45 треков' == embed.title


def test_queue_embed_stays_within_discord_limit():
    """20 длинных названий с URL раньше выбивали лимит description в 4096"""
    long_title = 'Очень длинное название трека, которое встречается у японских релизов ' * 2
    items = [track(title=long_title, artist=long_title, url='https://music.youtube.com/watch?v=abcdefghijk')
             for _ in range(20)]
    embed = build_queue_embed(items, page=0, page_size=20, max_page=0)
    assert len(embed.description) <= _DESCRIPTION_LIMIT
    assert len(embed.description) <= 4096


def test_queue_embed_escapes_brackets_in_label():
    items = [track(title='Song [Remix]', artist='', url='https://x/y')]
    desc = build_queue_embed(items, page=0, page_size=20, max_page=0).description
    assert '[Song (Remix)]' in desc


def test_queue_embed_without_url_is_plain_text():
    desc = build_queue_embed([track(title='S', artist='', url='')], page=0, page_size=20, max_page=0).description
    assert desc == '` 1.` S'


# --- view -------------------------------------------------------------------

def test_queue_view_pagination_state():
    items = [track(title=f'S{i}') for i in range(45)]
    view = QueueView(lambda: items, page_size=20, owner_id=1)
    assert view.max_page == 2
    assert view.first.disabled and view.prev.disabled
    assert not view.next.disabled
    view.page = 2
    view._sync_buttons()
    assert view.next.disabled and view.last.disabled


def test_queue_view_refresh_clamps_page():
    items = [track() for _ in range(45)]
    view = QueueView(lambda: items, page_size=20, owner_id=1)
    view.page = 2
    items[:] = [track()]
    view._refresh_items()
    assert view.page == 0 and view.max_page == 0


def test_queue_view_single_page_disables_all():
    view = QueueView(lambda: [track()], page_size=20, owner_id=1)
    assert all(b.disabled for b in (view.first, view.prev, view.next, view.last))


# --- эмбеды добавления ------------------------------------------------------

def test_added_track_embed():
    embed = build_added_track_embed(track(title='S', artist='A', url='https://open.spotify.com/track/x'))
    assert '## [S](https://open.spotify.com/track/x)' in embed.description
    assert 'A' in embed.description
    # источник в описании, а не в футере: кастомные эмодзи футер не рендерит
    assert 'Источник: Spotify' in embed.description
    assert embed.footer.text is None


def test_added_playlist_embed_album_wording():
    info = PlaylistInfo(tracks=[track(), track()], title='AL',
                        url='https://open.spotify.com/album/x', kind='album')
    embed = build_added_playlist_embed(info)
    assert embed.author.name == 'Добавлен альбом'
    assert '2 треков' in embed.description


def test_added_album_embed_shows_artist():
    """У альбома исполнитель один и показывается так же, как у трека"""
    info = PlaylistInfo(tracks=[track()], title='RUBY POP', artist='AiNA THE END',
                        url='https://open.spotify.com/album/x', kind='album')
    lines = build_added_playlist_embed(info).description.splitlines()
    assert lines[0].startswith('## [RUBY POP]')
    assert lines[1] == 'AiNA THE END', 'артист отдельной строкой, как в карточке трека'
    assert lines[2].startswith('-# 1 треков')


def test_added_playlist_embed_omits_artist():
    """У плейлиста исполнители разные, строки быть не должно"""
    info = PlaylistInfo(tracks=[track()], title='PL', url='u', kind='playlist')
    assert build_added_playlist_embed(info).description.splitlines() == [
        '## [PL](u)', '-# 1 треков']


def test_added_playlist_embed_marks_shuffle():
    info = PlaylistInfo(tracks=[track()], title='PL', url='u', kind='playlist')
    assert '(перемешано)' in build_added_playlist_embed(info, shuffled=True).author.name


@pytest.mark.parametrize('next_up,expected', [
    (False, 'Добавлено в очередь'),
    (True, 'Играет следующим'),
])
def test_added_track_embed_marks_next_up(next_up, expected):
    """Пользователь должен видеть разницу между «в конец» и «следующим»"""
    assert build_added_track_embed(track(), next_up=next_up).author.name == expected


@pytest.mark.parametrize('next_up,head', [
    (False, 'Добавлен плейлист'),
    (True, 'Играет следующим плейлист'),
])
def test_added_playlist_embed_marks_next_up(next_up, head):
    info = PlaylistInfo(tracks=[track()], title='PL', url='u', kind='playlist')
    assert build_added_playlist_embed(info, next_up=next_up).author.name == head


# --- режим повтора в карточках ------------------------------------------------

class _Src:
    """Минимальный источник: карточке нужна только длительность"""

    def __init__(self, duration=200.0):
        self.data = {'duration': duration}


@pytest.mark.parametrize('repeat,mark', [
    ('off', ''), ('track', '🔂'), ('queue', '🔁'),
])
def test_now_playing_shows_repeat(repeat, mark):
    """Выключенный повтор не показываем: строка прогресса и так плотная"""
    embed = build_now_playing_embed(track(title='S'), _Src(), 10.0, repeat=repeat)
    line = [x for x in embed.description.splitlines() if '▶️' in x][0]
    if mark:
        assert mark in line
    else:
        assert '🔂' not in line and '🔁' not in line


def test_current_track_embed_has_no_repeat_mark():
    """Снапшот показывает трек, а не состояние плеера"""
    t = track(title='S', url='https://www.youtube.com/watch?v=x')
    embed = build_current_track_embed(t, _Src())
    assert embed.description.splitlines()[-1] == 'Источник: YouTube'
    assert '🔂' not in embed.description and '🔁' not in embed.description


def test_repeat_survives_unknown_mode():
    """Чужое значение не должно ронять отрисовку"""
    embed = build_now_playing_embed(track(), _Src(), 5.0, repeat='мусор')
    assert embed.description


# --- иконки сервисов ----------------------------------------------------------

@pytest.fixture
def with_emojis():
    """Разметка, какой её отдаёт fetch_application_emojis"""
    from widgets.format import set_emojis
    set_emojis({'spotify': '<:spotify:1>', 'youtube_music': '<:youtube_music:2>'})
    yield
    set_emojis({})


def test_source_line_without_emojis():
    """До загрузки иконок строка остаётся текстовой, и это рабочее состояние"""
    from widgets.format import source_line
    assert source_line('https://open.spotify.com/track/x') == 'Источник: Spotify'
    assert source_line('https://example.com') == ''
    assert source_line('') == ''


def test_source_line_with_emojis(with_emojis):
    from widgets.format import source_line
    assert source_line('https://open.spotify.com/track/x') == 'Источник: Spotify <:spotify:1>'
    assert source_line('https://music.youtube.com/watch?v=x') == 'Источник: YouTube Music <:youtube_music:2>'
    # логотип не загружен — остаётся текст, а не пустая строка
    assert source_line('https://music.yandex.ru/track/1') == 'Источник: Yandex Music'


def test_every_label_has_an_emoji_name():
    """Иначе сервис молча останется без иконки после загрузки"""
    from widgets.format.source import EMOJI_NAMES, _RULES
    missing = {label for _, label in _RULES} - set(EMOJI_NAMES)
    assert not missing, f'нет имени эмодзи для: {missing}'


def test_now_playing_shows_source_in_description(with_emojis):
    """Источник обязан быть в описании: в футере иконка не отрисуется"""
    t = track(title='S', url='https://open.spotify.com/track/x')
    embed = build_now_playing_embed(t, _Src(), 10.0)
    assert embed.description.splitlines()[-1] == 'Источник: Spotify <:spotify:1>'
    assert embed.footer.text is None
