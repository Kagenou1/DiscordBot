"""Скачивание ffmpeg и deno в third_party/ под текущую платформу.

Запуск: python install_third_party.py

Куда кладёт:
- ffmpeg -> third_party/ffmpeg/bin/(ffmpeg|ffmpeg.exe)
- deno   -> third_party/(deno|deno.exe)

Если бинарник уже на месте — пропускает.
"""
import io
import os
import platform
import shutil
import stat
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
THIRD_PARTY = ROOT / 'third_party'


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
    chunks: list[bytes] = []
    total = 0
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
    return b''.join(chunks)


def _chmod_exec(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


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
            member = next(m for m in z.namelist() if m.endswith('/bin/ffmpeg.exe'))
            with z.open(member) as src, open(target, 'wb') as dst:
                shutil.copyfileobj(src, dst)

    elif os_name == 'linux':
        suffix = 'arm64' if arch == 'arm64' else 'amd64'
        url = f'https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-{suffix}-static.tar.xz'
        with tarfile.open(fileobj=io.BytesIO(download(url)), mode='r:xz') as t:
            member = next(m for m in t.getmembers() if m.name.endswith('/ffmpeg') and m.isfile())
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
    url = f'https://github.com/denoland/deno/releases/latest/download/{asset}'

    with zipfile.ZipFile(io.BytesIO(download(url))) as z:
        with z.open(bin_name) as src, open(target, 'wb') as dst:
            shutil.copyfileobj(src, dst)
    if os_name != 'windows':
        _chmod_exec(target)
    print(f'deno: установлен в {target}')


def main() -> None:
    os_name, arch = detect_platform()
    print(f'Платформа: {os_name} {arch}')
    THIRD_PARTY.mkdir(exist_ok=True)
    install_ffmpeg(os_name, arch)
    install_deno(os_name, arch)
    print('Готово.')


if __name__ == '__main__':
    main()
