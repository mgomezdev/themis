"""Bambu camera feed (X1 series: RTSP on :322). Core only fans out what the client produces; the ffmpeg RTSP -> MJPEG transcode
lives here because only this vendor needs it (BIZ-251)."""
from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncGenerator

from ...config import get_ffmpeg_executable

CHUNK_SIZE = 8192


def ffmpeg_available() -> bool:
    return bool(shutil.which(get_ffmpeg_executable()))


async def grab_rtsp_frame(rtsp_url: str, timeout: float = 12.0) -> bytes:
    """Grab a single JPEG frame from an RTSP (or RTSPS) stream via ffmpeg."""
    ffmpeg = get_ffmpeg_executable()
    proc = await asyncio.create_subprocess_exec(
        ffmpeg,
        "-rtsp_transport", "tcp",
        "-i", rtsp_url,
        "-vframes", "1",
        "-f", "image2",
        "-vcodec", "mjpeg",
        "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        raise ValueError("RTSP frame grab timed out")
    if proc.returncode != 0 or not stdout:
        raise ValueError(f"ffmpeg RTSP grab failed (exit {proc.returncode})")
    return stdout


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
