"""Loopback-only Manyfold wire fixture for the isolated browser harness, never a deployment service."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlsplit

BASE = 'http://127.0.0.1:18072'
FILE = '/models/model-one/model_files/file-one'


def start():
    state = {'version': 1, 'downloads': 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # OAuth credentials must not appear in test logs either.

        def reply(self, body, status=200, headers=None):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json' if not isinstance(body, bytes) else 'model/stl')
            self.send_header('Content-Length', str(len(data)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            data = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            if self.path == '/__fixture/version':
                state['version'] = json.loads(data)['version']
                return self.reply({'ok': True})
            form = parse_qs(data.decode())
            if self.path == '/oauth/token' and form == {
                'grant_type': ['client_credentials'], 'client_id': ['browser-client'],
                'client_secret': ['browser-fixture-secret'], 'scope': ['public read'],
            }:
                return self.reply({'access_token': 'browser-fixture-token', 'expires_in': 7200})
            self.reply({'error': 'Invalid fixture token request'}, 401)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/__fixture/stats':
                return self.reply(state)
            if self.headers.get('Authorization') != 'Bearer browser-fixture-token':
                return self.reply({'error': 'Unauthorized'}, 401)
            if path == '/models':
                return self.reply({'member': [{'@id': BASE + '/models/model-one'}], 'view': {}})
            if path == '/models/model-one':
                return self.reply({'@id': BASE + path, 'hasPart': [{'@id': BASE + FILE}], 'isPartOf': [],
                    'creator': {'@id': BASE + '/creators/artist'}, 'spdx:license': {'licenseId': 'CC-BY-4.0'}})
            if path == '/creators/artist':
                return self.reply({'name': 'Browser fixture artist', 'links': []})
            if path == FILE:
                return self.reply({'@id': BASE + FILE, 'filename': 'Browser source part.stl',
                    'isPartOf': {'@id': BASE + '/models/model-one'}, 'encodingFormat': 'model/stl',
                    'contentUrl': BASE + '/models/model-one/raw/part.stl'})
            if path == '/models/model-one/raw/part.stl':
                state['downloads'] += 1
                version = state['version']
                return self.reply(f'solid version{version}\nendsolid version{version}\n'.encode(), headers={'ETag': f'v{version}'})
            self.reply({'error': 'Not found'}, 404)

    server = ThreadingHTTPServer(('127.0.0.1', 18072), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
