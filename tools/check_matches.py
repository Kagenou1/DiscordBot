"""Проверка подбора YT-эквивалента для треков Spotify

Запуск:
    python tools/check_matches.py <ссылка или id плейлиста> [--limit N]
    python tools/check_matches.py <ссылка на трек>
    python tools/check_matches.py --tracks "Артист - Название" ...

Печатает только проблемные треки: выбранную запись, её исполнителя и причину.
Код возврата 1, если проблемы найдены — годится для регулярного прогона.
"""
import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audio.spotify.client import sp
from audio.youtube import search as S
from audio.youtube.client import ytm


# длительность расходится сильнее — почти наверняка другая запись
DUR_TOLERANCE = 12.0
# сколько ждать ответа о выбранной записи, прежде чем признать её непроверенной
DESCRIBE_BUDGET = 3.0

_SPOTIFY_ID = re.compile(r'(?:playlist|track|album)/(\w+)')


def _spotify_id(value: str) -> str:
    m = _SPOTIFY_ID.search(value)
    return m.group(1) if m else value


def _kind(value: str) -> str:
    for k in ('playlist', 'track', 'album'):
        if f'/{k}/' in value:
            return k
    return 'playlist'


def _item_to_row(item):
    if not item or not item.get('name'):
        return None
    return {
        'title': item['name'],
        'artist': ', '.join(a['name'] for a in (item.get('artists') or [])),
        'duration': (item.get('duration_ms') or 0) / 1000.0,
    }


def load_tracks(target: str, limit: int) -> list[dict]:
    kind, sid = _kind(target), _spotify_id(target)
    if kind == 'track':
        row = _item_to_row(sp.track(sid))
        return [row] if row else []
    if kind == 'album':
        items = (sp.album(sid).get('tracks') or {}).get('items') or []
        return [r for r in map(_item_to_row, items) if r][:limit]

    out, offset = [], 0
    while len(out) < limit:
        page = sp.playlist_items(sid, limit=100, offset=offset, additional_types=('track',))
        items = page.get('items') or []
        if not items:
            break
        for entry in items:
            row = _item_to_row(entry.get('item') or entry.get('track'))
            if row:
                out.append(row)
            if len(out) >= limit:
                break
        offset += 100
    return out


def parse_manual(values: list[str]) -> list[dict]:
    rows = []
    for v in values:
        artist, _, title = v.partition(' - ')
        rows.append({'title': title or v, 'artist': artist if title else '', 'duration': 0.0})
    return rows


def describe(vid: str):
    """Что лежит по videoId, либо None если узнать не удалось

    None и «нашлось, но не то» — разные вещи. YT Music срывается примерно на 5%
    запросов, и если это записывать как чужого исполнителя, отчёт врёт:
    на прогоне 200 треков так получилась 21 претензия вместо реальных 5
    """
    deadline = time.monotonic() + DESCRIBE_BUDGET
    while True:
        try:
            t = ytm.get_watch_playlist(vid, limit=1)['tracks'][0]
        except Exception:
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.25)
            continue
        return {
            'title': t.get('title') or '?',
            'artists': [{'name': a.get('name'), 'id': a.get('id')} for a in (t.get('artists') or [])],
            'duration': t.get('length'),
        }


def _seconds(text) -> float | None:
    """'3:48' или '1:02:03' в секунды"""
    if not text:
        return None
    try:
        parts = [int(x) for x in str(text).split(':')]
    except ValueError:
        return None
    total = 0.0
    for part in parts:
        total = total * 60 + part
    return total


def check(row: dict) -> dict:
    title, artist, dur = row['title'], row['artist'], row['duration']
    S._lookup.cache_clear()
    t0 = time.perf_counter()
    vid = S.ytm_catalog_lookup(title, artist, dur)
    elapsed = (time.perf_counter() - t0) * 1000

    res = {'запрос': f'{artist} — {title}'.strip(' —'), 'videoId': vid,
           'мс': round(elapsed), 'проблемы': [], 'непроверен': False}
    if not vid:
        res['проблемы'].append('ничего не найдено')
        return res

    got = describe(vid)
    if got is None:
        # это не претензия к подбору: мы просто не смогли посмотреть, что выбрано
        res['непроверен'] = True
        return res

    res['выбрано'] = got['title']
    res['исполнитель'] = ', '.join(a['name'] or '?' for a in got['artists'])

    aid = S._artist_id(artist) if artist else None
    if aid:
        if not any(a['id'] == aid for a in got['artists']):
            if S._artist_sim(S._norm(res['исполнитель']), S._norm(artist)) < 0.6:
                res['проблемы'].append('чужой исполнитель')
    elif artist:
        if S._artist_sim(S._norm(res['исполнитель']), S._norm(artist)) < 0.6:
            res['проблемы'].append('чужой исполнитель (артист не резолвится)')

    want = S._deriv_markers(title)
    have = S._deriv_markers(got['title'])
    if have != want:
        res['проблемы'].append(f'вид версии: просили {sorted(want) or "оригинал"}, '
                               f'получили {sorted(have) or "оригинал"}')

    got_s = _seconds(got['duration'])
    if dur and got_s and abs(got_s - dur) > DUR_TOLERANCE:
        res['проблемы'].append(f'длительность {got_s:.0f}с против {dur:.0f}с')
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description='Проверка подбора YT-эквивалента')
    ap.add_argument('target', nargs='?', help='ссылка или id плейлиста, альбома, трека')
    ap.add_argument('--limit', type=int, default=50)
    ap.add_argument('--tracks', nargs='*', help='вручную: "Артист - Название"')
    ap.add_argument('--json', help='куда сложить полный отчёт')
    ap.add_argument('--all', action='store_true', help='печатать и удачные')
    args = ap.parse_args()

    if args.tracks:
        rows = parse_manual(args.tracks)
    elif args.target:
        rows = load_tracks(args.target, args.limit)
    else:
        ap.error('нужен target или --tracks')

    print(f'проверяю {len(rows)} треков\n', flush=True)
    results, stat = [], Counter()
    t0 = time.perf_counter()
    for i, row in enumerate(rows, 1):
        try:
            res = check(row)
        except Exception as exc:
            res = {'запрос': f"{row['artist']} — {row['title']}", 'проблемы': [f'сбой: {exc!r}'[:70]]}
        results.append(res)
        stat['всего'] += 1
        if res.get('проблемы'):
            stat['проблемных'] += 1
            for p in res['проблемы']:
                stat[p.split(':')[0]] += 1
            print(f"  [!] {res['запрос'][:52]}")
            print(f"      -> {res.get('выбрано', '—')[:52]!r} / {res.get('исполнитель', '—')[:26]}")
            print(f"      {'; '.join(res['проблемы'])}  ({res.get('мс', 0)} мс)", flush=True)
        elif res.get('непроверен'):
            stat['непроверенных'] += 1
            print(f"  [?] {res['запрос'][:52]} — YT Music не ответил, что выбрано "
                  f"({res.get('videoId')})", flush=True)
        elif args.all:
            print(f"  ok  {res['запрос'][:52]} -> {res.get('выбрано', '')[:40]!r}", flush=True)

    n = stat['всего'] or 1
    bad, unknown = stat['проблемных'], stat['непроверенных']
    checked = n - unknown
    print()
    print(f'===== {n} треков за {time.perf_counter() - t0:.0f}с =====')
    if checked:
        print(f'  проверено     {checked:>4}  из них без проблем {checked - bad} '
              f'({(checked - bad) / checked * 100:.1f}%)')
    else:
        print('  проверено        0')
    print(f"  проблемных    {bad:>4}")
    print(f"  непроверенных {unknown:>4}  сбой YT Music, к подбору отношения не имеет")
    for key, cnt in stat.most_common():
        if key not in ('всего', 'проблемных', 'непроверенных'):
            print(f'     {cnt:>3}  {key}')

    if args.json:
        Path(args.json).write_text(json.dumps(results, ensure_ascii=False, indent=1),
                                   encoding='utf-8')
        print(f'\nполный отчёт: {args.json}')
    return 1 if stat['проблемных'] else 0


if __name__ == '__main__':
    sys.exit(main())
