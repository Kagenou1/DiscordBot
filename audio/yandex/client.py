"""Инициализация Yandex Music клиента, синглтон на процесс"""
import logging

from yandex_music import Client

try:
    from private import yandex_token
except ImportError:
    yandex_token = None


_log = logging.getLogger('audio').info


try:
    yandex_client: 'Client | None' = Client(yandex_token).init() if yandex_token else None
    if yandex_client is None:
        print('yandex_token не задан в private.py — провайдер Yandex Music выключен.')
except Exception as _exc:
    print(f'yandex_music unavailable: {_exc!r}')
    yandex_client = None
