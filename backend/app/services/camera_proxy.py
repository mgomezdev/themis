from __future__ import annotations
import asyncio
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

import httpx

from ..config import get_ffmpeg_executable

if TYPE_CHECKING:
    from .abstract_printer_client import AbstractPrinterClient

CHUNK_SIZE = 8192
_MAX_SNAPSHOT_BYTES = 2_000_000  # 2 MB


async def stream_mjpeg(url: str) -> AsyncGenerator[bytes, None]:
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("GET", url) as response:
            async for chunk in response.aiter_bytes(CHUNK_SIZE):
                yield chunk


async def grab_jpeg_frame(url: str, timeout: float = 8.0) -> bytes:
    """Connect to an MJPEG URL and return the first complete JPEG frame."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("GET", url) as response:
            buf = b""
            soi = -1
            async for chunk in response.aiter_bytes(CHUNK_SIZE):
                buf += chunk
                if soi == -1:
                    soi = buf.find(b'\xff\xd8')
                if soi != -1:
                    eoi = buf.find(b'\xff\xd9', soi + 2)
                    if eoi != -1:
                        return buf[soi:eoi + 2]
                if len(buf) > _MAX_SNAPSHOT_BYTES:
                    raise ValueError("JPEG frame exceeds size limit")
    raise ValueError("No complete JPEG frame found in stream")


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


async def grab_snapshot_from_client(client: "AbstractPrinterClient") -> bytes | None:
    """Normalize camera sources: return a JPEG snapshot for any printer type, or None."""
    if client.camera_mjpeg_url:
        return await grab_jpeg_frame(client.camera_mjpeg_url)
    if client.camera_rtsp_url:
        return await grab_rtsp_frame(client.camera_rtsp_url)
    return None


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
