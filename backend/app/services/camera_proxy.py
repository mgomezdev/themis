from __future__ import annotations
import asyncio
from collections.abc import AsyncGenerator

import httpx

from ..config import get_ffmpeg_executable

CHUNK_SIZE = 8192


async def stream_mjpeg(url: str) -> AsyncGenerator[bytes, None]:
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("GET", url) as response:
            async for chunk in response.aiter_bytes(CHUNK_SIZE):
                yield chunk


async def stream_rtsp_ffmpeg(rtsp_url: str) -> AsyncGenerator[bytes, None]:
    ffmpeg = get_ffmpeg_executable()
    proc = await asyncio.create_subprocess_exec(
        ffmpeg,
        "-rtsp_transport", "tcp",
        "-i", rtsp_url,
        "-f", "mpjpeg",
        "-q:v", "5",
        "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        while True:
            chunk = await proc.stdout.read(CHUNK_SIZE)
            if not chunk:
                break
            yield chunk
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
