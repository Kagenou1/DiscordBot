"""Проверка: отдаёт ли VK полный трек по токену или только фрагмент

Запуск:
    .venv/Scripts/python tools/check_vk_stream.py <ссылка на трек VK>
    .venv/Scripts/python tools/check_vk_stream.py -26549346_456239443_59159cef5d080f5450

Берёт vk_token и vk_user_agent из private.py, зовёт audio.getById, скачивает
плейлист и сравнивает сумму EXTINF с заявленной длительностью.

Анонимно VK кладёт фрагмент ~31 с при заявленных сотнях секунд, и в метаданных
это никак не помечено. Смысл проверки — узнать, снимает ли токен это ограничение
"""
import re
import sys

import requests

sys.path.insert(0, __file__.rsplit('tools', 1)[0])

try:
    from private import vk_token, vk_user_agent
except ImportError:
    sys.exit('В private.py нет vk_token / vk_user_agent — сначала tools/get_vk_token.py')

API = 'https://api.vk.com/method/audio.getById'
API_VERSION = '5.131'
# доля от заявленной длительности, ниже которой это фрагмент, а не трек
FULL_RATIO = 0.9

_AUDIO_ID = re.compile(r'audio(-?\d+_\d+(?:_[0-9a-f]+)?)')


def _audio_id(value: str) -> str:
    m = _AUDIO_ID.search(value)
    return m.group(1) if m else value


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith('--proxy')]
    if not args:
        sys.exit('Нужна ссылка на трек VK или его идентификатор')
    audio_id = _audio_id(args[0])
    print(f'идентификатор: {audio_id}')

    # Маршрут обязан совпадать с тем, которым брали токен: VK подписывает
    # ссылку на поток адресом запросившего, и разные выходы дадут 403
    proxy = next((a.split('=', 1)[1] for a in sys.argv[1:]
                  if a.startswith('--proxy=')), None)
    proxies = {'http': proxy, 'https': proxy} if proxy else None
    if proxy:
        print(f'маршрут: {proxy}')

    headers = {'User-Agent': vk_user_agent}
    resp = requests.post(API, params={
        'audios': audio_id,
        'access_token': vk_token,
        'v': API_VERSION,
    }, headers=headers, proxies=proxies, timeout=30)
    data = resp.json()

    if 'error' in data:
        err = data['error']
        sys.exit(f'VK отказал: {err.get("error_code")} {err.get("error_msg")}')

    items = data.get('response') or []
    if not items:
        sys.exit('Пустой ответ: трек недоступен для этого аккаунта')

    item = items[0]
    duration = item.get('duration') or 0
    url = item.get('url') or ''
    print(f'трек     : {item.get("artist")!r} — {item.get("title")!r}')
    print(f'длит.    : {duration} c')
    print(f'url      : {url[:96] or "ПУСТОЙ"}')

    if not url:
        sys.exit('URL пустой — обычно значит, что аккаунту трек не отдают')

    if '.m3u8' not in url:
        print('\nВЕРДИКТ: прямой файл, не HLS. Проверить длительность нечем — '
              'скормите ссылку ffmpeg')
        return

    body = requests.get(url, headers=headers, proxies=proxies, timeout=30).text
    served = sum(float(x) for x in re.findall(r'#EXTINF:([\d.]+)', body))
    segments = len(re.findall(r'#EXTINF:', body))
    encrypted = body.count('METHOD=AES-128')
    print(f'сегментов: {segments}, из них шифрованных ключей: {encrypted}')
    print(f'в плейлисте: {served:.1f} c из {duration} c '
          f'({served / duration * 100 if duration else 0:.0f}%)')

    if duration and served >= duration * FULL_RATIO:
        print('\nВЕРДИКТ: ПОЛНЫЙ ТРЕК. Токен снимает ограничение, провайдер имеет смысл')
    else:
        print('\nВЕРДИКТ: ФРАГМЕНТ. Токен ограничение не снимает, путь тупиковый')


if __name__ == '__main__':
    main()
