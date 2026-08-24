"""Скачивание ffmpeg, deno и провайдера PO Token в third_party/

Запуск: python install_third_party.py

Куда кладёт:
- ffmpeg   -> third_party/ffmpeg/bin/(ffmpeg|ffmpeg.exe)
- deno     -> third_party/(deno|deno.exe)
- node   -> third_party/node/(node.exe|bin/node)
- bgutil -> third_party/bgutil/server/

node и bgutil нужны серверу выдачи PO Token: с токеном YouTube отдаёт opus
по ссылкам, валидным сразу, иначе старт трека откладывается на секунды.
Сервер бот поднимает сам при прогреве. Если node не найден, шаг пропускается
и бот работает без токена — просто медленнее.

Если бинарник уже на месте, шаг пропускается.
Скрипт печатает SHA-256 каждого архива; чтобы зафиксировать сборку, впишите
хеш в EXPECTED_SHA256 — дальше он будет проверяться и расхождение прервёт установку
"""
import hashlib
import io
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
THIRD_PARTY = ROOT / 'third_party'

# deno берём по фиксированной версии: latest молча меняет содержимое под тем же URL
DENO_VERSION = 'v2.5.3'
# node нужен провайдеру PO Token: его BotGuard под deno работает нестабильно
NODE_VERSION = 'v24.19.0'
# версия провайдера обязана совпадать с версией pip-пакета bgutil-ytdlp-pot-provider
BGUTIL_VERSION = '1.3.1'

# url -> ожидаемый sha256 архива; пустой словарь = проверка выключена
EXPECTED_SHA256: dict[str, str] = {}


def detect_platform() -> tuple[str, str]:
    system = platform.system()
    machine = platform.machine().lower()
    if system == 'Windows':
        return 'windows', 'arm64' if 'arm' in machine else 'x64'
    if system == 'Linux':
        if machine in ('x86_64', 'amd64'):
            return 'linux', 'x64'
        if machine in ('aarch64', 'arm64'):
            return 'linux', 'arm64'
    if system == 'Darwin':
        return 'macos', 'arm64' if machine in ('arm64', 'aarch64') else 'x64'
    raise RuntimeError(f'Неподдерживаемая платформа: {system}/{machine}')


def download(url: str) -> bytes:
    print(f'  GET {url}')
    if not url.lower().startswith('https://'):
        raise RuntimeError(f'Отказ: небезопасная схема в {url}')
    chunks: list[bytes] = []
    total = 0
    size = 0
    with urllib.request.urlopen(url) as r:
        size = int(r.headers.get('Content-Length') or 0)
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if size:
                pct = total * 100 // size
                sys.stdout.write(f'\r  {total // 1024} / {size // 1024} KB ({pct}%)')
                sys.stdout.flush()
    if size:
        sys.stdout.write('\n')
    blob = b''.join(chunks)
    _verify(url, blob)
    return blob


def _verify(url: str, blob: bytes) -> None:
    digest = hashlib.sha256(blob).hexdigest()
    expected = EXPECTED_SHA256.get(url)
    if expected is None:
        print(f'  sha256 {digest}')
        return
    if digest != expected:
        raise RuntimeError(
            f'Хеш не совпал для {url}\n  ожидали {expected}\n  получили {digest}'
        )
    print(f'  sha256 {digest} (совпал)')


def _chmod_exec(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _safe_member(name: str) -> bool:
    """Отсечь пути с .. и абсолютные: архив распаковывается в наш каталог"""
    p = Path(name)
    return not p.is_absolute() and '..' not in p.parts


def install_ffmpeg(os_name: str, arch: str) -> None:
    bin_name = 'ffmpeg.exe' if os_name == 'windows' else 'ffmpeg'
    target_dir = THIRD_PARTY / 'ffmpeg' / 'bin'
    target = target_dir / bin_name
    if target.exists():
        print(f'ffmpeg: уже на месте ({target})')
        return

    target_dir.mkdir(parents=True, exist_ok=True)

    if os_name == 'windows':
        url = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
        with zipfile.ZipFile(io.BytesIO(download(url))) as z:
            member = next(m for m in z.namelist() if m.endswith('/bin/ffmpeg.exe') and _safe_member(m))
            with z.open(member) as src, open(target, 'wb') as dst:
                shutil.copyfileobj(src, dst)

    elif os_name == 'linux':
        suffix = 'arm64' if arch == 'arm64' else 'amd64'
        url = f'https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-{suffix}-static.tar.xz'
        with tarfile.open(fileobj=io.BytesIO(download(url)), mode='r:xz') as t:
            member = next(
                m for m in t.getmembers()
                if m.name.endswith('/ffmpeg') and m.isfile() and _safe_member(m.name)
            )
            extracted = t.extractfile(member)
            if extracted is None:
                raise RuntimeError('Не удалось извлечь ffmpeg из tar.xz')
            with open(target, 'wb') as dst:
                shutil.copyfileobj(extracted, dst)
        _chmod_exec(target)

    elif os_name == 'macos':
        url = 'https://evermeet.cx/ffmpeg/getrelease/ffmpeg/zip'
        with zipfile.ZipFile(io.BytesIO(download(url))) as z:
            with z.open('ffmpeg') as src, open(target, 'wb') as dst:
                shutil.copyfileobj(src, dst)
        _chmod_exec(target)

    print(f'ffmpeg: установлен в {target}')


def install_deno(os_name: str, arch: str) -> None:
    bin_name = 'deno.exe' if os_name == 'windows' else 'deno'
    target = THIRD_PARTY / bin_name
    if target.exists():
        print(f'deno: уже на месте ({target})')
        return

    THIRD_PARTY.mkdir(parents=True, exist_ok=True)

    cpu = 'aarch64' if arch == 'arm64' else 'x86_64'
    targets = {
        'windows': f'{cpu}-pc-windows-msvc',
        'linux': f'{cpu}-unknown-linux-gnu',
        'macos': f'{cpu}-apple-darwin',
    }
    asset = f'deno-{targets[os_name]}.zip'
    url = f'https://github.com/denoland/deno/releases/download/{DENO_VERSION}/{asset}'

    with zipfile.ZipFile(io.BytesIO(download(url))) as z:
        with z.open(bin_name) as src, open(target, 'wb') as dst:
            shutil.copyfileobj(src, dst)
    if os_name != 'windows':
        _chmod_exec(target)
    print(f'deno: установлен в {target} ({DENO_VERSION})')


def _node_paths(os_name: str) -> tuple[Path, Path]:
    """(корень установки, путь к исполняемому node)"""
    root = THIRD_PARTY / 'node'
    exe = root / 'node.exe' if os_name == 'windows' else root / 'bin' / 'node'
    return root, exe


def _system_node() -> str | None:
    """Подходящий node уже в PATH — качать свой незачем"""
    found = shutil.which('node')
    if not found:
        return None
    try:
        out = subprocess.run([found, '--version'], capture_output=True, text=True,
                             timeout=10).stdout.strip()
        major = int(out.lstrip('v').split('.')[0])
    except Exception:
        return None
    return found if major >= 20 else None


def install_node(os_name: str, arch: str) -> None:
    root, exe = _node_paths(os_name)
    if exe.exists():
        print(f'node: уже на месте ({exe})')
        return
    if system := _system_node():
        print(f'node: берём системный ({system})')
        return

    cpu = 'arm64' if arch == 'arm64' else 'x64'
    names = {'windows': f'node-{NODE_VERSION}-win-{cpu}',
             'linux': f'node-{NODE_VERSION}-linux-{cpu}',
             'macos': f'node-{NODE_VERSION}-darwin-{cpu}'}
    stem = names[os_name]
    ext = 'zip' if os_name == 'windows' else 'tar.gz'
    url = f'https://nodejs.org/dist/{NODE_VERSION}/{stem}.{ext}'
    blob = download(url)

    tmp = THIRD_PARTY / '_node_tmp'
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    if ext == 'zip':
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            z.extractall(tmp, members=[m for m in z.namelist() if _safe_member(m)])
    else:
        with tarfile.open(fileobj=io.BytesIO(blob), mode='r:gz') as tf:
            tf.extractall(tmp, members=[m for m in tf.getmembers() if _safe_member(m.name)])

    if root.exists():
        shutil.rmtree(root)
    (tmp / stem).rename(root)
    shutil.rmtree(tmp, ignore_errors=True)
    if os_name != 'windows':
        _chmod_exec(exe)
    print(f'node: установлен в {root} ({NODE_VERSION})')


def install_pot_provider(os_name: str) -> None:
    """Исходники провайдера + его зависимости + сборка скрипта генерации"""
    home = THIRD_PARTY / 'bgutil' / 'server'
    script = home / 'build' / 'generate_once.js'
    if script.exists():
        print(f'провайдер PO Token: уже на месте ({script})')
        return

    _, node_exe = _node_paths(os_name)
    if not node_exe.exists():
        system = _system_node()
        if not system:
            print('провайдер PO Token: пропуск, node не найден')
            return
        node_exe = Path(system)

    url = ('https://github.com/Brainicism/bgutil-ytdlp-pot-provider/archive/'
           f'refs/tags/{BGUTIL_VERSION}.zip')
    blob = download(url)
    tmp = THIRD_PARTY / '_bgutil_tmp'
    if tmp.exists():
        shutil.rmtree(tmp)
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        z.extractall(tmp, members=[m for m in z.namelist() if _safe_member(m)])

    src = tmp / f'bgutil-ytdlp-pot-provider-{BGUTIL_VERSION}' / 'server'
    if home.exists():
        shutil.rmtree(home)
    home.parent.mkdir(parents=True, exist_ok=True)
    src.rename(home)
    shutil.rmtree(tmp, ignore_errors=True)

    # npm и npx лежат рядом с node. Зовём их полным путём: CreateProcess не ищет
    # .cmd по PATH, а PATH правим всё равно — он нужен самим npm и npx, чтобы
    # найти node
    env = dict(os.environ)
    env['PATH'] = str(node_exe.parent) + os.pathsep + env.get('PATH', '')
    suffix = '.cmd' if os_name == 'windows' else ''
    npm = str(node_exe.parent / f'npm{suffix}')
    npx = str(node_exe.parent / f'npx{suffix}')
    for step, cmd in (('зависимости', [npm, 'ci', '--no-audit', '--no-fund', '--loglevel=error']),
                      ('сборка', [npx, 'tsc'])):
        print(f'  провайдер PO Token: {step}...')
        r = subprocess.run(cmd, cwd=home, env=env, capture_output=True, text=True)
        if r.returncode != 0:
            print(f'  не удалось ({step}): {(r.stderr or r.stdout)[:300]}')
            return
    print(f'провайдер PO Token: установлен в {home} ({BGUTIL_VERSION})')


def main() -> None:
    os_name, arch = detect_platform()
    print(f'Платформа: {os_name} {arch}')
    THIRD_PARTY.mkdir(exist_ok=True)
    install_ffmpeg(os_name, arch)
    install_deno(os_name, arch)
    install_node(os_name, arch)
    install_pot_provider(os_name)
    print('Готово.')


if __name__ == '__main__':
    main()
