from setuptools import setup, find_packages

setup(
    name='DiscordBot',
    version='0.1.0',
    packages=find_packages(include=('audio*', 'widgets*', 'cogs*')),
    python_requires='>=3.11',
    install_requires=[
        'discord.py[voice]>=2.7',
        'yt-dlp>=2025.1.1',
        'ytmusicapi>=1.10',
        'spotipy>=2.24',
        'yandex-music>=2.2',
        'audioop-lts>=0.2; python_version >= "3.13"',
    ],
    extras_require={
        'fast': ['winloop>=0.6; sys_platform == "win32"'],
        'dev': ['pytest>=8.0', 'pytest-asyncio>=0.24'],
    },
    url='',
    license='',
    author='Kagenou',
    author_email='',
    description='Музыкальный бот для Discord: YouTube, Spotify, Yandex Music, SoundCloud',
)
