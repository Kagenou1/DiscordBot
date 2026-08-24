"""Локальный HTTP CONNECT-прокси для трафика VK

Зачем: ffmpeg понимает только HTTP CONNECT (опция -http_proxy) и не умеет SOCKS5.
Поток VK тянет именно он, отдельным процессом, поэтому нужен локальный приёмник,
через который пойдут и запросы к API, и сам звук.

Выход обязан быть общим для того и другого: VK подписывает ссылку на поток тем
адресом, который её запросил, и разные выходы дадут 403 на живой ссылке.

Два режима вывода:

  --direct        соединяться напрямую. Смысл в том, чтобы ЭТОТ процесс был
                  исключён из VPN (в AmneziaVPN — исключение по приложениям):
                  тогда VK выходит через домашний адрес, а бот целиком остаётся
                  в туннеле. Исключать python.exe нельзя, им запущен сам бот —
                  нужен отдельный файл, например .venv/Scripts/vkproxy.exe

  socks5://...    уводить в SOCKS5, если российский выход это отдельный прокси
                  или VPS

Запуск:
    .venv/Scripts/vkproxy.exe tools/vk_proxy.py --direct
    .venv/Scripts/python tools/vk_proxy.py socks5://user:pass@host:1080
    .venv/Scripts/vkproxy.exe tools/vk_proxy.py --direct --check

--check показывает, каким адресом виден трафик, ушедший через прокси: так
проверяется, что исключение в VPN действительно сработало
"""
import argparse
import select
import socket
import sys
import threading
import urllib.parse


_CONNECT_TIMEOUT = 20
_BUF = 65536
# Сколько байт держать прочитанными вперёд. Сегмент HLS ВКонтакте — сотни
# килобайт, так что потолка хватает на несколько сегментов; он существует
# только чтобы застрявший клиент не съел память
_MAX_AHEAD = 16 * 1024 * 1024
# Как часто TCP проверяет живость молчащего пира (Windows, мс)
_KEEPALIVE_IDLE = 60_000
_KEEPALIVE_INTERVAL = 10_000


def _keepalive(sock: socket.socket) -> None:
    """Проверять живость молчащего пира силами TCP

    Таймер простоя тут не годится: заготовка следующего трека набирает 30 с
    звука, упирается в полный буфер и замирает посреди скачивания сегмента.
    Соединение молчит, пока играет предыдущий трек, то есть минуты. Тайм-аут
    в 60 с рвал его, и заготовка доигрывала только из буфера, дальше шли дыры
    """
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if hasattr(socket, 'SIO_KEEPALIVE_VALS'):
            sock.ioctl(socket.SIO_KEEPALIVE_VALS,
                       (1, _KEEPALIVE_IDLE, _KEEPALIVE_INTERVAL))
    except OSError:
        pass


def parse_socks(url: str) -> dict:
    """socks5://user:pass@host:port -> аргументы для socksocket.set_proxy"""
    import socks

    parts = urllib.parse.urlparse(url)
    scheme = (parts.scheme or 'socks5').lower()
    if scheme not in ('socks5', 'socks5h', 'socks4', 'socks4a'):
        raise ValueError(f'Неподдерживаемая схема прокси: {scheme!r}')
    if not parts.hostname:
        raise ValueError(f'В адресе прокси нет хоста: {url!r}')
    # rdns: имя резолвит прокси. Иначе DNS ушёл бы мимо российского выхода
    # и VK увидел бы запрос из другого региона
    return {
        'proxy_type': socks.SOCKS4 if scheme.startswith('socks4') else socks.SOCKS5,
        'addr': parts.hostname,
        'port': parts.port or 1080,
        'rdns': True,
        'username': urllib.parse.unquote(parts.username) if parts.username else None,
        'password': urllib.parse.unquote(parts.password) if parts.password else None,
    }


def _pipe(a: socket.socket, b: socket.socket) -> None:
    """Гнать байты в обе стороны, пока не закроются обе

    Читаем с опережением. Прямая пересылка (recv у одного, sendall другому)
    связывает скорость CDN со скоростью чтения: заготовка следующего трека
    набирает буфер и замирает, ffmpeg перестаёт читать, соединение простаивает
    посреди ответа — и CDN ВКонтакте его закрывает. Замер на паузе 300 с:
    241.5 с звука из 248 и битые пакеты. Забирая ответ целиком, мы даём CDN
    закрыть соединение нормально, а ffmpeg дочитывает из памяти.

    Опережение ограничено _MAX_AHEAD: упёршись в потолок, перестаём читать —
    это возвращает прежнее поведение, но уже на осознанном объёме.

    Закрывать оба сокета по EOF одной стороны нельзя: у второй остаются
    непрочитанные данные, и close() тогда шлёт RST вместо FIN. ffmpeg видит
    «Stream ends prematurely», сегмент обрывается, и в звуке появляется дыра.
    Поэтому на EOF дошлём хвост и закроем только запись встречной стороне.

    Тайм-аута простоя нет: туннель заканчивается, когда его закроет одна из
    сторон, и молчание само по себе поводом не является. Пира, пропавшего
    без FIN, обнаружит keepalive
    """
    peer = {a: b, b: a}
    pending = {a: bytearray(), b: bytearray()}  # что ждёт отправки в этот сокет
    readable = {a, b}                           # у кого ещё не было EOF
    try:
        while True:
            # читаем, пока опережение не упёрлось в потолок
            rlist = [s for s in readable if len(pending[peer[s]]) < _MAX_AHEAD]
            wlist = [s for s in (a, b) if pending[s]]
            if not rlist and not wlist:
                break
            ready_r, ready_w, _ = select.select(rlist, wlist, [])
            for src in ready_r:
                data = src.recv(_BUF)
                if not data:
                    readable.discard(src)
                    continue
                pending[peer[src]] += data
            for dst in ready_w:
                sent = dst.send(pending[dst])
                del pending[dst][:sent]
            # хвост дослан и читать у встречной стороны больше нечего
            for dst in (a, b):
                if not pending[dst] and peer[dst] not in readable:
                    try:
                        dst.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
    except OSError:
        pass


class LocalProxy:
    """HTTP CONNECT на локальном порту; наружу напрямую или через SOCKS5"""

    def __init__(self, upstream=None, host: str = '127.0.0.1', port: int = 0):
        self._socks = parse_socks(upstream) if upstream else None
        self._host = host
        self._srv = socket.socket()
        # SO_REUSEADDR на Windows разрешает второму процессу сесть на занятый
        # порт и молча перехватывать соединения. Нужно обратное: второй
        # экземпляр обязан упасть громко
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((host, port))
        self._srv.listen(32)
        self.port = self._srv.getsockname()[1]
        self._stop = threading.Event()

    @property
    def url(self) -> str:
        """Значение для ffmpeg -http_proxy и для requests"""
        return f'http://{self._host}:{self.port}'

    def start(self):
        threading.Thread(target=self._serve, daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass

    def _connect_upstream(self, host: str, port: int) -> socket.socket:
        if self._socks is None:
            return socket.create_connection((host, port), timeout=_CONNECT_TIMEOUT)
        import socks

        sock = socks.socksocket()
        sock.set_proxy(**self._socks)
        sock.settimeout(_CONNECT_TIMEOUT)
        sock.connect((host, port))
        return sock

    def _open_plain(self, request: list, head: bytes):
        """Открыть соединение для обычного http и переписать строку запроса

        Клиент шлёт «GET http://host/path HTTP/1.1», серверу нужна origin-форма
        «GET /path HTTP/1.1». Keep-alive выключаем: держать пул соединений
        этому прокси незачем, а без него проще не потерять границу ответа
        """
        parts = urllib.parse.urlsplit(request[1])
        if not parts.hostname:
            return None, head
        target = urllib.parse.urlunsplit(('', '', parts.path or '/',
                                          parts.query, ''))
        rest = head.split(b'\r\n', 1)[1] if b'\r\n' in head else b''
        rebuilt = ' '.join([request[0], target, request[2] if len(request) > 2
                            else 'HTTP/1.1']).encode('latin1')
        lines = [ln for ln in rest.split(b'\r\n')
                 if not ln.lower().startswith((b'proxy-connection:', b'connection:'))]
        rest = b'\r\n'.join(lines)
        head = rebuilt + b'\r\n' + rest.replace(b'\r\n\r\n',
                                                b'\r\nConnection: close\r\n\r\n', 1)
        return self._connect_upstream(parts.hostname, parts.port or 80), head

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        upstream = None
        try:
            head = b''
            conn.settimeout(_CONNECT_TIMEOUT)
            while b'\r\n\r\n' not in head:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                head += chunk
                if len(head) > 16384:
                    return
            line = head.split(b'\r\n', 1)[0].decode('latin1', 'replace')
            request = line.split()
            if len(request) < 2:
                conn.sendall(b'HTTP/1.1 400 Bad Request\r\n\r\n')
                return

            if request[0].upper() == 'CONNECT':
                host, _, port = request[1].rpartition(':')
                if not host:
                    host, port = request[1], '443'
                upstream = self._connect_upstream(host, int(port or 443))
                conn.sendall(b'HTTP/1.1 200 Connection established\r\n\r\n')
            else:
                # Обычный http: клиент шлёт запрос с абсолютным URI. Нужен не
                # ради VK (он весь на https), а чтобы браузер, пущенный через
                # этот прокси, не считал сеть неисправной из-за своих фоновых
                # http-проверок
                upstream, head = self._open_plain(request, head)
                if upstream is None:
                    conn.sendall(b'HTTP/1.1 400 Bad Request\r\n\r\n')
                    return
                upstream.sendall(head)

            conn.settimeout(None)
            upstream.settimeout(None)
            _keepalive(conn)
            _keepalive(upstream)
            _pipe(conn, upstream)
        except Exception as exc:
            # молчаливый отказ неотличим от сетевой ошибки на той стороне:
            # ffmpeg увидит только 5XX и причины не покажет
            print(f'vk_proxy: {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
            try:
                conn.sendall(b'HTTP/1.1 502 Bad Gateway\r\n\r\n')
            except OSError:
                pass
        finally:
            for sock in (conn, upstream):
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass


def _check(proxy: LocalProxy) -> int:
    """Показать, каким адресом виден трафик, ушедший через прокси"""
    import requests

    proxies = {'http': proxy.url, 'https': proxy.url}
    # Адреса обязаны быть https: на обычный HTTP клиент шлёт запрос с абсолютным
    # URI, а этот прокси умеет только CONNECT и ответит 405.
    # Список, а не один адрес: часть таких сервисов из России недоступна, и
    # единственный адрес превращал бы проверку в лотерею
    services = [
        ('https://api.myip.com', 'ip', 'cc'),
        ('https://ipapi.co/json/', 'ip', 'country_code'),
        ('https://ifconfig.co/json', 'ip', 'country_iso'),
        ('https://ipinfo.io/json', 'ip', 'country'),
    ]
    ip = code = None
    for url, ip_key, cc_key in services:
        try:
            data = requests.get(url, proxies=proxies, timeout=12).json()
        except Exception as exc:
            print(f'  {url} не ответил: {type(exc).__name__}')
            continue
        ip, code = data.get(ip_key), data.get(cc_key)
        if ip:
            break
    if not ip:
        print('проверка не удалась: ни один сервис не ответил. Если наружу не '
              'проходит ничего, кроме порта 53, значит трафик режет kill switch')
        return 1
    print(f'через прокси видны как: {ip}  страна {code}')
    if code == 'RU':
        print('ОК: выход российский, VK пойдёт правильным маршрутом')
        return 0
    print('ВНИМАНИЕ: выход не российский. Если ждали обратного — процесс ещё не '
          'исключён из VPN либо исключение сделано не для того файла')
    return 1


def main() -> None:
    ap = argparse.ArgumentParser(description='Локальный HTTP CONNECT-прокси для VK')
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument('--direct', action='store_true',
                     help='наружу напрямую; процесс исключается из VPN')
    src.add_argument('upstream', nargs='?',
                     help='адрес SOCKS5, например socks5://127.0.0.1:1080')
    ap.add_argument('--port', type=int, default=8890,
                    help='локальный порт (0 — любой)')
    ap.add_argument('--check', action='store_true',
                    help='показать страну выхода и завершиться')
    args = ap.parse_args()

    proxy = LocalProxy(None if args.direct else args.upstream, port=args.port).start()
    print(f'vk_proxy слушает {proxy.url}, наружу '
          f'{"напрямую" if args.direct else args.upstream}')

    if args.check:
        code = _check(proxy)
        proxy.stop()
        sys.exit(code)

    print('для ffmpeg:  -http_proxy ' + proxy.url)
    print('Ctrl+C для остановки')
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        proxy.stop()


if __name__ == '__main__':
    main()
