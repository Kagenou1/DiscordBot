"""SoundCloud не требует отдельного прогрева — yt-dlp уже греется в youtube.warm_up."""


async def warm_up(loop) -> None:
    return None
