"""A Moonraker file_manager, at the `httpx.get/delete/post` surface the Snapmaker client uses. Endpoints and
response shapes follow the Moonraker web API docs (root `gcodes`, `/server/files/directory?extended=true`,
`/server/files/gcodes/<path>`, `/printer/print/start`)."""
from __future__ import annotations

import urllib.parse

import httpx


class VirtualMoonraker:
    def __init__(self, files: dict[str, dict] | None = None, api_key: str | None = None) -> None:
        # path (relative to gcodes) -> {"data": bytes, "modified": float, "meta": {estimated_time, ...}}
        self.files = files or {}
        self.api_key = api_key
        self.started: list[str] = []
        self.requests: list[tuple[str, str]] = []
        self.print_state = "standby"          # Klipper print_stats.state, driven by start/pause/resume/cancel
        self.gcode_scripts: list[str] = []
        self.fail_paths: dict[str, int] = {}  # path -> HTTP status to answer with (error injection)

    def _resp(self, req: httpx.Request, status: int, body) -> httpx.Response:
        return httpx.Response(status, json=body, request=req) if isinstance(body, (dict, list)) \
            else httpx.Response(status, content=body, request=req)

    def handle(self, method: str, url: str, params: dict | None = None, headers: dict | None = None,
               files: dict | None = None, **_) -> httpx.Response:
        req = httpx.Request(method, url, params=params)
        parsed = urllib.parse.urlparse(url)
        path = urllib.parse.unquote(parsed.path)
        self.requests.append((method, path))
        if path in self.fail_paths:
            return self._resp(req, self.fail_paths[path], {"error": {"code": self.fail_paths[path], "message": "injected failure"}})
        if self.api_key and (headers or {}).get("X-Api-Key") != self.api_key:
            return self._resp(req, 401, {"error": {"code": 401, "message": "Unauthorized"}})

        if method == "GET" and path == "/server/files/directory":
            root = (params or {}).get("path", "gcodes")
            if root != "gcodes" and not root.startswith("gcodes/"):
                return self._resp(req, 404, {"error": {"code": 404, "message": "Root not found"}})
            rel = root[len("gcodes"):].strip("/")
            prefix = f"{rel}/" if rel else ""
            dirs, files = {}, []
            for p, f in sorted(self.files.items()):
                if not p.startswith(prefix):
                    continue
                rest = p[len(prefix):]
                if "/" in rest:
                    dirs.setdefault(rest.split("/")[0], {"modified": f["modified"], "size": 4096,
                                                         "permissions": "rw", "dirname": rest.split("/")[0]})
                else:
                    entry = {"modified": f["modified"], "size": len(f["data"]), "permissions": "rw", "filename": rest}
                    if (params or {}).get("extended") == "true":
                        entry.update(f.get("meta", {}))
                    files.append(entry)
            if rel and not dirs and not files:
                return self._resp(req, 404, {"error": {"code": 404, "message": f"Directory {root} does not exist"}})
            return self._resp(req, 200, {"result": {"dirs": list(dirs.values()), "files": files,
                                                    "disk_usage": {"total": 1, "used": 1, "free": 1},
                                                    "root_info": {"name": "gcodes", "permissions": "rw"}}})

        if path.startswith("/server/files/gcodes/"):
            rel = path[len("/server/files/gcodes/"):]
            if rel not in self.files:
                return self._resp(req, 404, {"error": {"code": 404, "message": "File not found"}})
            if method == "GET":
                return self._resp(req, 200, self.files[rel]["data"])
            if method == "DELETE":
                del self.files[rel]
                return self._resp(req, 200, {"result": {"item": {"path": rel, "root": "gcodes"}, "action": "delete_file"}})

        if method == "GET" and path == "/server/info":
            return self._resp(req, 200, {"result": {"klippy_state": "ready", "moonraker_version": "v0.9.3-virtual"}})

        if method == "POST" and path == "/server/files/upload":
            name, data = files["file"][0], files["file"][1]
            self.files[name] = {"data": data, "modified": 1_759_100_000.0}
            return self._resp(req, 201, {"item": {"path": name, "root": "gcodes"}, "action": "create_file"})

        if method == "POST" and path == "/printer/print/start":
            self.started.append((params or {}).get("filename", ""))
            self.print_state = "printing"
            return self._resp(req, 200, {"result": "ok"})
        transitions = {"/printer/print/pause": ("printing", "paused"), "/printer/print/resume": ("paused", "printing"),
                       "/printer/print/cancel": (None, "cancelled")}
        if method == "POST" and path in transitions:
            needs, to = transitions[path]
            if needs is not None and self.print_state != needs:
                return self._resp(req, 400, {"error": {"code": 400, "message": f"Klippy Request Error: not {needs}"}})
            self.print_state = to
            return self._resp(req, 200, {"result": "ok"})
        if method == "POST" and path == "/printer/gcode/script":
            self.gcode_scripts.append((params or {}).get("script", ""))
            return self._resp(req, 200, {"result": "ok"})
        return self._resp(req, 404, {"error": {"code": 404, "message": f"Not found: {method} {path}"}})


class _StreamCtx:
    def __init__(self, resp: httpx.Response) -> None:
        self._r = resp

    def __enter__(self):
        return self._r

    def __exit__(self, *exc):
        return False


def install(monkeypatch, server: VirtualMoonraker):
    monkeypatch.setattr(httpx, "stream", lambda method, url, **kw: _StreamCtx(server.handle(method, url, **kw)))
    monkeypatch.setattr(httpx, "get", lambda url, **kw: server.handle("GET", url, **kw))
    monkeypatch.setattr(httpx, "delete", lambda url, **kw: server.handle("DELETE", url, **kw))
    monkeypatch.setattr(httpx, "post", lambda url, **kw: server.handle("POST", url, **kw))


def status_frame(**objects) -> str:
    """A `notify_status_update` WebSocket frame carrying these Klipper objects (what Moonraker pushes after subscribe)."""
    import json
    return json.dumps({"jsonrpc": "2.0", "method": "notify_status_update", "params": [objects, 1.0]})


class FakeWebSocket:
    """Captures what a client sends over the Moonraker WebSocket (JSON-RPC)."""
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False

    def send(self, text: str) -> None:
        import json
        self.sent.append(json.loads(text))

    def close(self) -> None:
        self.closed = True

    def methods(self) -> list[str]:
        return [m["method"] for m in self.sent]


def make_client(api_key: str | None = None):
    from app.plugins.snapmaker.client import SnapmakerExtendedClient
    return SnapmakerExtendedClient(ip_address="192.0.2.20", api_key=api_key)
