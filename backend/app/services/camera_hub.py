"""Shares one upstream camera connection between every viewer.

A camera wall opens many streams at once and several browsers may watch the same printer. Without sharing, each
viewer costs one printer-side connection (Elegoo and Bambu cameras only tolerate one or two) and, for RTSP, one
ffmpeg transcode. The hub keeps ONE upstream per printer, cuts it into whole JPEG frames, and fans each frame out to
every subscriber's small queue; a slow viewer drops its own oldest frames, never stalls the others. The upstream is
closed when the last viewer leaves. Snapshots are served from the live stream's latest frame when there is one, and
otherwise from a short cache with concurrent requests coalesced into a single grab.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

BOUNDARY = "frame"
MAX_FRAME_BYTES = 2_000_000
QUEUE_FRAMES = 2                       # a viewer more than this far behind skips ahead
SNAPSHOT_TTL_S = 1.0                   # concurrent/rapid snapshot requests share a grab this fresh
LIVE_FRAME_MAX_AGE_S = 3.0             # a live stream's latest frame this fresh answers snapshots too
KEEPALIVE_S = 45.0                     # Elegoo drops a silent MJPEG stream after 60 s


def max_streams() -> int:
    """Upper bound on simultaneous upstream camera connections (THEMIS_MAX_CAMERA_STREAMS, default 64)."""
    try:
        return max(1, int(os.environ.get("THEMIS_MAX_CAMERA_STREAMS", "64")))
    except ValueError:
        return 64


class HubFull(Exception):
    """Too many distinct upstream camera streams are already open."""


def multipart_part(jpeg: bytes) -> bytes:
    return (f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n").encode() + jpeg + b"\r\n"


class FrameSplitter:
    """Extracts whole JPEG frames (SOI…EOI) from an arbitrary byte stream, whether raw concatenated JPEGs or
    multipart MJPEG — the part headers/boundaries between frames are simply skipped."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> list[bytes]:
        self._buf += chunk
        frames: list[bytes] = []
        while True:
            soi = self._buf.find(b"\xff\xd8")
            if soi == -1:
                self._buf.clear()
                break
            if soi > 0:
                del self._buf[:soi]
            eoi = self._buf.find(b"\xff\xd9", 2)
            if eoi == -1:
                if len(self._buf) > MAX_FRAME_BYTES:      # runaway/garbage: resynchronise
                    self._buf.clear()
                break
            frames.append(bytes(self._buf[:eoi + 2]))
            del self._buf[:eoi + 2]
        return frames


@dataclass
class _Stream:
    key: int
    task: asyncio.Task | None = None
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    latest: bytes | None = None
    latest_at: float = 0.0
    started_at: float = field(default_factory=time.time)
    frames: int = 0
    bytes_in: int = 0
    bytes_out: int = 0


Source = Callable[[], AsyncIterator[bytes]]


class CameraHub:
    def __init__(self) -> None:
        self._streams: dict[int, _Stream] = {}
        self._snap: dict[int, tuple[float, bytes]] = {}
        self._inflight: dict[int, asyncio.Future] = {}
        self.snapshot_hits = 0
        self.snapshot_grabs = 0

    # ---- streaming ----
    async def subscribe(self, key: int, source: Source, on_keepalive: Callable[[], None] | None = None
                        ) -> AsyncIterator[bytes]:
        """Yield multipart parts for printer `key`, starting (or joining) its single shared upstream."""
        stream = self._streams.get(key)
        if stream is None:
            if len(self._streams) >= max_streams():
                raise HubFull(f"{len(self._streams)} camera streams already open")
            stream = self._streams[key] = _Stream(key)
            stream.task = asyncio.create_task(self._pump(stream, source, on_keepalive))
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_FRAMES)
        stream.subscribers.add(q)
        try:
            if stream.latest is not None and time.monotonic() - stream.latest_at < LIVE_FRAME_MAX_AGE_S:
                self._offer(q, stream.latest)                 # joiner sees a picture immediately
            while True:
                jpeg = await q.get()
                if jpeg is None:
                    return
                part = multipart_part(jpeg)
                stream.bytes_out += len(part)
                yield part
        finally:
            stream.subscribers.discard(q)
            if not stream.subscribers and self._streams.get(key) is stream:
                self._close(stream)

    @staticmethod
    def _offer(q: asyncio.Queue, item) -> None:
        while True:
            try:
                q.put_nowait(item)
                return
            except asyncio.QueueFull:
                try:
                    q.get_nowait()                            # drop that viewer's oldest frame
                except asyncio.QueueEmpty:
                    pass

    def _close(self, stream: _Stream) -> None:
        if self._streams.get(stream.key) is stream:
            del self._streams[stream.key]
        if stream.task and not stream.task.done():
            stream.task.cancel()

    async def _pump(self, stream: _Stream, source: Source, on_keepalive) -> None:
        splitter = FrameSplitter()
        ka = asyncio.create_task(self._keepalive(on_keepalive)) if on_keepalive else None
        try:
            async for chunk in source():
                stream.bytes_in += len(chunk)
                for jpeg in splitter.feed(chunk):
                    stream.latest, stream.latest_at = jpeg, time.monotonic()
                    stream.frames += 1
                    for q in list(stream.subscribers):
                        self._offer(q, jpeg)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Camera stream %s ended with an error", stream.key, exc_info=True)
        finally:
            if ka:
                ka.cancel()
            if self._streams.get(stream.key) is stream:
                del self._streams[stream.key]
            for q in list(stream.subscribers):
                self._offer(q, None)                          # upstream gone: end every viewer's response

    @staticmethod
    async def _keepalive(fn: Callable[[], None]) -> None:
        while True:
            await asyncio.sleep(KEEPALIVE_S)
            try:
                fn()
            except Exception:
                logger.debug("camera keepalive failed", exc_info=True)

    # ---- snapshots ----
    async def snapshot(self, key: int, grab: Callable[[], Awaitable[bytes | None]]) -> bytes | None:
        """Latest live frame if a stream is open, else a ≤1 s-old cached grab, else one grab shared by every
        concurrent caller."""
        stream = self._streams.get(key)
        if stream and stream.latest is not None and time.monotonic() - stream.latest_at < LIVE_FRAME_MAX_AGE_S:
            self.snapshot_hits += 1
            return stream.latest
        cached = self._snap.get(key)
        if cached and time.monotonic() - cached[0] < SNAPSHOT_TTL_S:
            self.snapshot_hits += 1
            return cached[1]
        pending = self._inflight.get(key)
        if pending is not None:
            self.snapshot_hits += 1
            return await asyncio.shield(pending)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._inflight[key] = fut
        try:
            self.snapshot_grabs += 1
            jpeg = await grab()
            if jpeg is not None:
                self._snap[key] = (time.monotonic(), jpeg)
            fut.set_result(jpeg)
            return jpeg
        except BaseException as exc:
            fut.set_exception(exc if isinstance(exc, Exception) else RuntimeError("cancelled"))
            fut.exception()                                   # mark retrieved when nobody else was waiting
            raise
        finally:
            self._inflight.pop(key, None)

    def is_full_for(self, key: int) -> bool:
        return key not in self._streams and len(self._streams) >= max_streams()

    def forget(self, key: int) -> None:
        """Printer removed/disconnected: end its stream and drop cached frames."""
        self._snap.pop(key, None)
        stream = self._streams.get(key)
        if stream:
            for q in list(stream.subscribers):
                self._offer(q, None)
            self._close(stream)

    def stats(self) -> dict:
        return {
            "streams": [{
                "printer_id": s.key, "viewers": len(s.subscribers), "frames": s.frames,
                "bytes_in": s.bytes_in, "bytes_out": s.bytes_out,
                "uptime_s": round(time.time() - s.started_at, 1),
            } for s in self._streams.values()],
            "viewers": sum(len(s.subscribers) for s in self._streams.values()),
            "max_streams": max_streams(),
            "snapshot_hits": self.snapshot_hits, "snapshot_grabs": self.snapshot_grabs,
        }


hub = CameraHub()
