"""Shared camera upstreams: one connection for many viewers, frame-level fan-out, snapshot coalescing."""
import asyncio

import pytest

from app.services import camera_hub
from app.services.camera_hub import CameraHub, FrameSplitter, HubFull, multipart_part
from tests.waiting import wait_until


def jpeg(tag: str) -> bytes:
    return b"\xff\xd8" + tag.encode() + b"\xff\xd9"


def test_the_splitter_cuts_whole_frames_from_raw_and_multipart_bytes_across_chunk_boundaries():
    sp = FrameSplitter()
    data = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg("a") + b"\r\n--frame\r\n\r\n" + jpeg("b")
    out = []
    for i in range(0, len(data), 5):                      # worst case: tiny chunks
        out += sp.feed(data[i:i + 5])
    assert out == [jpeg("a"), jpeg("b")]
    assert sp.feed(b"\xff\xd8partial") == []              # incomplete frame is held, not emitted


def test_the_splitter_resynchronises_after_runaway_data(monkeypatch):
    monkeypatch.setattr(camera_hub, "MAX_FRAME_BYTES", 10)
    sp = FrameSplitter()
    assert sp.feed(b"\xff\xd8" + b"x" * 50) == []
    assert sp.feed(jpeg("ok")) == [jpeg("ok")]


class Source:
    """A controllable upstream: counts connections, lets the test push chunks."""
    def __init__(self):
        self.opened = 0
        self.closed = 0
        self.q: asyncio.Queue = asyncio.Queue()

    async def __call__(self):
        self.opened += 1
        try:
            while True:
                chunk = await self.q.get()
                if chunk is None:
                    return
                yield chunk
        finally:
            self.closed += 1


async def collect(hub, key, src, n, out):
    async for part in hub.subscribe(key, src):
        out.append(part)
        if len(out) >= n:
            return


async def test_many_viewers_share_one_upstream_and_each_gets_every_frame():
    hub, src = CameraHub(), Source()
    a, b = [], []
    ta = asyncio.create_task(collect(hub, 1, src, 2, a))
    tb = asyncio.create_task(collect(hub, 1, src, 2, b))
    await wait_until(lambda: src.opened == 1 and len(hub._streams[1].subscribers) == 2)
    await src.q.put(jpeg("1"))
    await src.q.put(jpeg("2"))
    await asyncio.wait_for(asyncio.gather(ta, tb), 3)
    assert src.opened == 1
    assert a == b == [multipart_part(jpeg("1")), multipart_part(jpeg("2"))]


async def test_the_upstream_closes_when_the_last_viewer_leaves_and_reopens_for_the_next():
    hub, src = CameraHub(), Source()
    out = []
    t = asyncio.create_task(collect(hub, 1, src, 1, out))
    await wait_until(lambda: src.opened == 1)
    await src.q.put(jpeg("1"))
    await t
    await wait_until(lambda: src.closed == 1 and 1 not in hub._streams)
    t2 = asyncio.create_task(collect(hub, 1, src, 1, []))
    await wait_until(lambda: src.opened == 2)
    t2.cancel()


async def test_a_slow_viewer_drops_its_own_old_frames_and_never_blocks_a_fast_one():
    hub, src = CameraHub(), Source()
    fast: list = []
    tf = asyncio.create_task(collect(hub, 1, src, 6, fast))
    slow_gen = hub.subscribe(1, src)
    slow_first = asyncio.create_task(slow_gen.__anext__())
    await wait_until(lambda: src.opened == 1 and len(hub._streams[1].subscribers) == 2)
    for i in range(6):
        await src.q.put(jpeg(str(i)))
        await asyncio.sleep(0)
    await asyncio.wait_for(tf, 3)
    assert len(fast) == 6
    assert (await asyncio.wait_for(slow_first, 3)) is not None
    slow_first2 = await asyncio.wait_for(slow_gen.__anext__(), 3)
    assert slow_first2 in [multipart_part(jpeg(str(i))) for i in range(6)]
    await slow_gen.aclose()


async def test_a_joining_viewer_gets_the_latest_frame_immediately():
    hub, src = CameraHub(), Source()
    first = []
    t1 = asyncio.create_task(collect(hub, 1, src, 1, first))
    await wait_until(lambda: src.opened == 1)
    await src.q.put(jpeg("now"))
    await t1
    late = []
    t2 = asyncio.create_task(collect(hub, 1, src, 1, late))
    # (stream may have closed with the first viewer; either way the late viewer gets a frame from a live upstream)
    await wait_until(lambda: src.opened >= 1)
    await src.q.put(jpeg("later"))
    await asyncio.wait_for(t2, 3)
    assert late and late[0].endswith(b"\xff\xd9\r\n")


async def test_an_upstream_that_ends_ends_every_viewer():
    hub, src = CameraHub(), Source()
    out = []
    t = asyncio.create_task(collect(hub, 1, src, 5, out))
    await wait_until(lambda: src.opened == 1)
    await src.q.put(None)
    await asyncio.wait_for(t, 3)
    assert out == [] and 1 not in hub._streams


async def test_the_stream_cap_refuses_a_new_printer_but_lets_existing_viewers_join(monkeypatch):
    monkeypatch.setenv("THEMIS_MAX_CAMERA_STREAMS", "1")
    hub, s1, s2 = CameraHub(), Source(), Source()
    t = asyncio.create_task(collect(hub, 1, s1, 99, []))
    await wait_until(lambda: s1.opened == 1)
    assert hub.is_full_for(2) is True and hub.is_full_for(1) is False
    with pytest.raises(HubFull):
        async for _ in hub.subscribe(2, s2):
            pass
    t2 = asyncio.create_task(collect(hub, 1, s1, 99, []))
    await wait_until(lambda: len(hub._streams[1].subscribers) == 2)
    t.cancel(); t2.cancel()


async def test_the_keepalive_fires_while_streaming(monkeypatch):
    monkeypatch.setattr(camera_hub, "KEEPALIVE_S", 0.01)
    hub, src = CameraHub(), Source()
    pings = []
    gen = hub.subscribe(1, src, lambda: pings.append(1))
    t = asyncio.create_task(gen.__anext__())
    await wait_until(lambda: len(pings) >= 2)
    t.cancel()
    await asyncio.gather(t, return_exceptions=True)


async def test_forget_ends_the_stream_and_clears_the_cache():
    hub, src = CameraHub(), Source()
    out = []
    t = asyncio.create_task(collect(hub, 1, src, 5, out))
    await wait_until(lambda: src.opened == 1)
    await hub.snapshot(1, _grab_returning(jpeg("s")))
    hub.forget(1)
    await asyncio.wait_for(t, 3)
    await wait_until(lambda: src.closed == 1)
    assert 1 not in hub._streams and 1 not in hub._snap


def _grab_returning(data, calls=None, delay=0.0):
    async def grab():
        if calls is not None:
            calls.append(1)
        if delay:
            await asyncio.sleep(delay)
        return data
    return grab


async def test_concurrent_snapshots_share_one_grab_and_a_rapid_repeat_hits_the_cache():
    hub, calls = CameraHub(), []
    results = await asyncio.gather(*[hub.snapshot(1, _grab_returning(jpeg("x"), calls, 0.05)) for _ in range(10)])
    assert results == [jpeg("x")] * 10 and len(calls) == 1
    assert await hub.snapshot(1, _grab_returning(jpeg("y"), calls)) == jpeg("x")      # ≤ 1 s old
    assert len(calls) == 1 and hub.snapshot_grabs == 1 and hub.snapshot_hits == 10


async def test_a_stale_cache_is_regrabbed_and_different_printers_do_not_share(monkeypatch):
    monkeypatch.setattr(camera_hub, "SNAPSHOT_TTL_S", 0)
    hub, calls = CameraHub(), []
    await hub.snapshot(1, _grab_returning(jpeg("a"), calls))
    await hub.snapshot(1, _grab_returning(jpeg("b"), calls))
    assert len(calls) == 2
    monkeypatch.setattr(camera_hub, "SNAPSHOT_TTL_S", 5)
    await hub.snapshot(1, _grab_returning(jpeg("c"), calls))
    assert await hub.snapshot(2, _grab_returning(jpeg("other"), calls)) == jpeg("other")


async def test_a_live_stream_answers_snapshots_without_grabbing():
    hub, src, calls = CameraHub(), Source(), []
    t = asyncio.create_task(collect(hub, 1, src, 99, []))
    await wait_until(lambda: src.opened == 1)
    await src.q.put(jpeg("live"))
    await wait_until(lambda: hub._streams[1].latest is not None)
    assert await hub.snapshot(1, _grab_returning(jpeg("grab"), calls)) == jpeg("live") and calls == []
    t.cancel()


async def test_a_failed_grab_reaches_every_waiter_and_is_not_cached():
    hub = CameraHub()

    async def boom():
        await asyncio.sleep(0.02)
        raise ValueError("camera down")

    results = await asyncio.gather(*[hub.snapshot(1, boom) for _ in range(3)], return_exceptions=True)
    assert all(isinstance(r, ValueError) for r in results)
    assert await hub.snapshot(1, _grab_returning(jpeg("ok"))) == jpeg("ok")
