# Camera wall & camera proxy (BIZ-162)

**Screen:** `/wall` (More → Camera wall). Every camera-capable printer in a grid; density (columns) selector; status
filter (All / Printing / Paused / Error / Idle / Offline); tile shows name, status, alarm badge (BIZ-157); click opens
`/fleet/:id/console`.

## Live vs snapshot
Browsers allow ~6 concurrent HTTP/1.1 connections per origin, and each live MJPEG `<img>` holds one for as long as it
is open. So the wall shows **at most N live streams** (selector: 0 / 4 / 8 / 16, default 4 — printing/paused tiles get
them first) and every other tile polls `/snapshot` (default every 5 s, 2 s for the live-limit-0 "all snapshots" mode
off). Over HTTP/2 (a TLS reverse proxy) the cap can be raised. A tile whose live stream errors (e.g. 429, camera
down) falls back to snapshots on its own.

## Proxy sharing (`services/camera_hub.py`)
- **One upstream per printer**, however many viewers: MJPEG printers see one connection, RTSP printers run **one
  ffmpeg** transcode. Closed when the last viewer leaves.
- The hub cuts upstream into whole JPEG frames and fans them out through 2-frame queues: a slow viewer drops its own
  frames, never stalls others.
- Snapshots: served from a live stream's latest frame if one is open; else a ≤ 1 s cache; concurrent requests share a
  single grab. 40 tiles × several browsers therefore cost ≤ 1 grab/printer/second.
- Cap: `THEMIS_MAX_CAMERA_STREAMS` (default 64) distinct upstreams; beyond it `/camera` answers 429 (the wall then falls
  back to snapshots).
- `GET /api/v1/cameras/stats` → open streams (viewers, frames, bytes in/out), snapshot hits vs real grabs.
- `/camera` and `/snapshot` accept `?key=` (an `<img src>` can't send headers).

## Load test
`python backend/scripts/camera_load_test.py --cameras 20 40 --viewers 1 3 --seconds 8 --unshared` runs fake MJPEG
cameras (10 fps, 40 KB frames ≈ 0.4 MB/s each) through the real hub + upstream client. One sandbox core, one process
that also runs the fake cameras and viewers (so CPU is an upper bound for the proxy alone):

| cameras | viewers/cam | shared | upstream conns | CPU % | in MB/s | out MB/s |
|---|---|---|---|---|---|---|
| 20 | 1 | yes | 20 | 13 | 7.6 | 7.5 |
| 20 | 3 | yes | 20 | 12 | 7.6 | 22.5 |
| 20 | 3 | no  | 60 | 34 | 18.7 | 18.5 (saturated) |
| 40 | 1 | yes | 40 | 23 | 14.1 | 13.9 |
| 40 | 3 | yes | 40 | 27 | 13.7 | 40.6 |
| 40 | 3 | no  | 120 | 43+ | saturated | saturated |

Takeaways: inbound bandwidth scales with *cameras* (≈ 0.35 MB/s each at 10 fps/40 KB), outbound with *viewers ×
cameras*; sharing keeps CPU flat as viewers are added. A 40-tile wall of live streams is ~14 MB/s in and out per
viewer — which is why the wall defaults to few live tiles + snapshots (a snapshot tile at 5 s ≈ 8 KB/s).
**Not measured:** ffmpeg RTSP→MJPEG CPU (depends on the camera's codec/resolution; budget roughly one core-fraction per
printer — run `top` against a real RTSP camera and record it here) and real Bambu/Elegoo cameras' own connection limits.
