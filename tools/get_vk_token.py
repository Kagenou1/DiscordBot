"""Получение токена VK для audio API через вход в браузере

Схема: implicit flow под клиентом Kate Mobile. Вход происходит на странице
VK ID в браузере, токен возвращается в адресной строке. Пароль в этот скрипт
не вводится вообще, 2FA и капчу обрабатывает сам VK.

Прежняя схема (grant_type=password, она же direct auth) МЕРТВА: VK отвечает
'9;Flood control' / 'password_bruteforce_attempt' на любую попытку — замерено
с несуществующим логином, с двух разных адресов, на обоих клиентах и с подменой
TLS-отпечатка под Chrome. Пароль и адрес значения не имеют, отказ безусловный.
Это сходится с тем, что OAuth 2.1 объявил password grant устаревшим.

Запуск:
    .venv/Scripts/python tools/get_vk_token.py
    .venv/Scripts/python tools/get_vk_token.py --client vk

Токен пишется в log/.vk-token (каталог в .gitignore) и в терминал не печатается —
перенести его в private.py нужно вручную.

ВАЖНО про маршрут: страницу входа надо открывать тем же выходом, которым потом
будет ходить бот, иначе VK увидит вход из одной страны и обращения из другой.
Поднимите прокси и запустите браузер через него:

    .venv/Scripts/vkproxy.exe tools/vk_proxy.py --direct
    chrome.exe --proxy-server="http://127.0.0.1:8890" --user-data-dir="%TEMP%\\vk-login"
"""
import argparse
import json
import os
import sys
import urllib.parse


AUTHORIZE_URL = 'https://oauth.vk.com/authorize'
REDIRECT_URI = 'https://oauth.vk.com/blank.html'
API_VERSION = '5.131'

# (User-Agent, client_id) мобильных клиентов VK. UA обязателен во всех
# последующих запросах: токен к нему привязан.
# Проверено 2026-08-19: authorize отвечает формой входа только для kate,
# у официального клиента и Boom — invalid_request
CLIENTS = {
    'kate': (
        'KateMobileAndroid/56 lite-460 (Android 4.4.2; SDK 19; x86; '
        'unknown Android SDK built for x86; en)',
        '2685278',
    ),
    'vk': (
        'VKAndroidApp/4.13.1-1206 (Android 4.4.3; SDK 19; armeabi; ; ru)',
        '2274003',
    ),
}

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(_ROOT, 'log', '.vk-token')

_STEPS = """
1. Убедитесь, что браузер ходит нужным маршрутом (см. шапку файла)
2. Откройте ссылку ниже и войдите в аккаунт
3. VK перебросит на пустую страницу blank.html — сама она ничего не покажет,
   всё нужное будет в АДРЕСНОЙ СТРОКЕ, после решётки
4. Скопируйте адрес целиком и вставьте сюда

Ссылка:
{url}
"""


def build_url(client_id: str) -> str:
    query = urllib.parse.urlencode({
        'client_id': client_id,
        'scope': 'audio,offline',   # offline делает токен бессрочным
        'redirect_uri': REDIRECT_URI,
        'display': 'page',
        'response_type': 'token',
        'revoke': 1,                # не переиспользовать прошлое разрешение
        'v': API_VERSION,
    })
    return f'{AUTHORIZE_URL}?{query}'


def parse_redirect(value: str) -> dict:
    """Достать поля из адреса вида blank.html#access_token=...&user_id=...

    Принимается и сам токен, вставленный отдельно
    """
    value = value.strip()
    if not value:
        return {}
    if '#' not in value and '=' not in value:
        return {'access_token': value}
    fragment = value.split('#', 1)[1] if '#' in value else value
    parsed = urllib.parse.parse_qs(fragment)
    return {k: v[0] for k, v in parsed.items() if v}


def save(token: str, ua: str, user_id: str = '') -> None:
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    payload = {'vk_token': token, 'vk_user_agent': ua}
    if user_id:
        payload['vk_user_id'] = user_id
    with open(OUT_PATH, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    try:
        os.chmod(OUT_PATH, 0o600)
    except OSError:
        pass


def main() -> None:
    ap = argparse.ArgumentParser(
        description='Получение токена VK для audio API через вход в браузере')
    ap.add_argument('--client', choices=sorted(CLIENTS), default='kate',
                    help='какой клиент эмулировать (по умолчанию kate — '
                         'у остальных authorize отвечает invalid_request)')
    args = ap.parse_args()
    ua, client_id = CLIENTS[args.client]

    print(f'Клиент: {args.client}')
    print('Используйте отдельный аккаунт, не основной: за обращения к audio API '
          'VK блокирует')
    print(_STEPS.format(url=build_url(client_id)))

    data = parse_redirect(input('Адрес из строки браузера: '))
    token = data.get('access_token')
    if not token:
        if data.get('error'):
            sys.exit(f'VK вернул ошибку: {data.get("error")} '
                     f'{data.get("error_description", "")}')
        sys.exit('В адресе нет access_token. Скопируйте строку целиком, '
                 'вместе с частью после решётки')

    expires = data.get('expires_in')
    if expires not in (None, '0'):
        print(f'ВНИМАНИЕ: токен ограничен по времени (expires_in={expires}). '
              f'Бессрочный выдаётся только со scope offline')

    save(token, ua, data.get('user_id', ''))
    print(f'\nТокен сохранён: {OUT_PATH}')
    print('В терминал он намеренно не выводится. Перенесите значения в private.py:')
    print('    vk_token = <...>')
    print('    vk_user_agent = <...>')
    print('После переноса файл лучше удалить')


if __name__ == '__main__':
    main()
