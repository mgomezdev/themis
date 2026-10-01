"""Camera proxy load test: N fake MJPEG cameras, V viewers each, through the real hub + httpx upstream code.

    python scripts/camera_load_test.py --cameras 20 40 --viewers 1 3 --fps 10 --kb 40 --seconds 10

Fake cameras are local HTTP servers emitting multipart MJPEG frames of --kb KB at --fps. Reports this process's CPU
(proxy + fake cameras + viewers share one core-bound event loop, so treat it as an UPPER bound for the proxy alone),
upstream connections opened (shared ⇒ == cameras, not cameras × viewers), and bytes in/out. RTSP→MJPEG transcode cost
(ffmpeg) is NOT measured here — it depends on the camera's codec/resolution; one ffmpeg per printer, shared, is the cap.
"""
import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import camera_hub                      # noqa: E402
from app.services.camera_proxy import stream_mjpeg        # noqa: E402


def make_frame(kb: int) -> bytes:
    return b"\xff\xd8" + b"\x11" * (kb * 1024) + b"\xff\xd9"


async def camera_server(port: int, fps: int, frame: bytes, counter: list):
    async def handle(reader, writer):
        counter[0] += 1
        await reader.readline()
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: multipart/x-mixed-replace; boundary=frame\r\n\r\n")
        try:
            while True:
                writer.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(frame) + frame + b"\r\n")
                await writer.drain()
                await asyncio.sleep(1 / fps)
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writer.close()
    return await asyncio.start_server(handle, "127.0.0.1", port)


async def run(cameras: int, viewers: int, fps: int, kb: int, seconds: float, share: bool) -> dict:
    frame, upstream_conns = make_frame(kb), [0]
    servers = [await camera_server(0, fps, frame, upstream_conns) for _ in range(cameras)]
    ports = [s.sockets[0].getsockname()[1] for s in servers]
    hub, received = camera_hub.CameraHub(), [0]

    async def viewer(i: int, v: int):
        key = i if share else i * 1000 + v                            # unshared: a distinct upstream per viewer
        src = lambda: stream_mjpeg(f"http://127.0.0.1:{ports[i]}/")   # noqa: E731
        async for part in hub.subscribe(key, src):
            received[0] += len(part)

    cpu0, t0 = time.process_time(), time.monotonic()
    tasks = [asyncio.create_task(viewer(i, v)) for i in range(cameras) for v in range(viewers)]
    await asyncio.sleep(seconds)
    stats = hub.stats()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    wall, cpu = time.monotonic() - t0, time.process_time() - cpu0
    for s in servers:
        s.close()
    in_mb = sum(s["bytes_in"] for s in stats["streams"]) / 1e6
    return {"cameras": cameras, "viewers/cam": viewers, "shared": share, "upstream conns": upstream_conns[0],
            "cpu %": round(100 * cpu / wall, 1), "in MB/s": round(in_mb / wall, 2),
            "out MB/s": round(received[0] / 1e6 / wall, 2)}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cameras", type=int, nargs="+", default=[20, 40])
    ap.add_argument("--viewers", type=int, nargs="+", default=[1, 3])
    ap.add_argument("--fps", type=int, default=10)
    ap.add_argument("--kb", type=int, default=40)
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--unshared", action="store_true", help="also run without sharing, for comparison")
    a = ap.parse_args()
    rows = []
    for c in a.cameras:
        for v in a.viewers:
            for share in ([True, False] if a.unshared else [True]):
                rows.append(await run(c, v, a.fps, a.kb, a.seconds, share))
                print(rows[-1], flush=True)
    print("\n| " + " | ".join(rows[0]) + " |\n|" + "---|" * len(rows[0]))
    for r in rows:
        print("| " + " | ".join(str(x) for x in r.values()) + " |")


if __name__ == "__main__":
    asyncio.run(main())
