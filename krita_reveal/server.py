"""
server.py — Local HTTP server for the Reveal web UI.

Serves ui/index.html and a small REST API.
Runs in a daemon thread; the Qt main thread drains the command queue.
"""

from __future__ import annotations

import json
import os
import queue
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

UI_DIR = os.path.join(os.path.dirname(__file__), 'ui')


# ── Shared state ───────────────────────────────────────────────────────────

class RevealState:
    """Thread-safe store shared between the HTTP thread and Qt main thread."""

    def __init__(self):
        self._lock               = threading.Lock()
        self.status              = 'idle'   # idle | running | done | error
        self.message             = ''
        self.is_error            = False
        self.preview_jpeg        = None     # bytes — current preview (post or solo)
        self.orig_jpeg           = None     # bytes — original image
        self.palette             = []       # [{r,g,b,hex,pct}, ...]
        self.has_result          = False
        self.result              = None     # full separation result dict
        self.preview_version     = 0        # increments whenever preview_jpeg changes
        self.archetypes          = []       # [{id,name,group,score}, ...] sorted best-first
        self.matched_archetype_id = ''
        self.matched_archetype   = {}      # {id,name,density,speckle,clamp} — actual values used
        self.suggestions         = []       # [{r,g,b,hex,reason,score}, ...]

    def set_running(self, msg):
        with self._lock:
            self.status   = 'running'
            self.message  = msg
            self.is_error = False

    def set_done(self, msg, preview_jpeg, orig_jpeg, palette, result,
                 archetypes=None, matched_archetype_id='', matched_archetype=None,
                 suggestions=None):
        with self._lock:
            self.status              = 'done'
            self.message             = msg
            self.is_error            = False
            self.preview_jpeg        = preview_jpeg
            self.orig_jpeg           = orig_jpeg
            self.palette             = palette
            self.has_result          = True
            self.result              = result
            self.preview_version     += 1
            self.archetypes          = archetypes or []
            self.matched_archetype_id = matched_archetype_id
            self.matched_archetype   = matched_archetype or {}
            self.suggestions         = list(suggestions or [])

    def set_preview(self, jpeg_bytes):
        with self._lock:
            self.preview_jpeg    = jpeg_bytes
            self.preview_version += 1

    def set_preview_and_palette(self, jpeg_bytes, palette):
        with self._lock:
            self.preview_jpeg    = jpeg_bytes
            self.palette         = palette
            self.preview_version += 1

    def set_error(self, msg):
        with self._lock:
            self.status   = 'error'
            self.message  = msg
            self.is_error = True

    def set_message(self, msg, is_error=False):
        with self._lock:
            self.message  = msg
            self.is_error = is_error

    def get_status(self):
        with self._lock:
            return {
                'status':               self.status,
                'message':              self.message,
                'is_error':             self.is_error,
                'palette':              list(self.palette),
                'has_result':           self.has_result,
                'archetypes':           list(self.archetypes),
                'matched_archetype_id': self.matched_archetype_id,
                'matched_archetype':    dict(self.matched_archetype),
                'suggestions':          list(self.suggestions),
            }

    def get_preview_jpeg(self):
        with self._lock:
            return self.preview_jpeg

    def get_orig_jpeg(self):
        with self._lock:
            return self.orig_jpeg


# ── HTTP handler ───────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass  # silence request log

    @property
    def _state(self):
        return self.server.reveal_state

    @property
    def _queue(self):
        return self.server.command_queue

    def do_GET(self):
        path = self.path.split('?')[0]
        if path in ('/', '/index.html'):
            self._serve_file(os.path.join(UI_DIR, 'index.html'), 'text/html; charset=utf-8')
        elif path == '/img/post':
            self._serve_bytes(self._state.get_preview_jpeg(), 'image/jpeg')
        elif path == '/img/orig':
            self._serve_bytes(self._state.get_orig_jpeg(), 'image/jpeg')
        elif path == '/api/status':
            self._serve_json(self._state.get_status())
        else:
            self._send_code(404)

    def do_POST(self):
        path   = self.path.split('?')[0]
        body   = self._read_body()
        params = json.loads(body) if body else {}

        if path == '/api/separate':
            self._state.set_running('Starting…')
            self._queue.put({'type': 'separate', 'params': params})
            self._serve_json({'ok': True})
        elif path == '/api/rerun':
            self._state.set_running('Applying archetype…')
            self._queue.put({'type': 'rerun', 'params': params})
            self._serve_json({'ok': True})
        elif path == '/api/solo':
            old_ver = self._state.preview_version
            self._queue.put({'type': 'solo', 'params': params})
            # Block until Qt main thread has rendered the new preview (max 2s)
            import time
            for _ in range(40):
                time.sleep(0.05)
                if self._state.preview_version != old_ver:
                    break
            self._serve_json({'ok': True})
        elif path == '/api/build':
            self._queue.put({'type': 'build'})
            self._serve_json({'ok': True})
        elif path == '/api/override':
            old_ver = self._state.preview_version
            self._queue.put({'type': 'override', 'params': params})
            import time
            for _ in range(40):
                time.sleep(0.05)
                if self._state.preview_version != old_ver:
                    break
            with self._state._lock:
                palette = list(self._state.palette)
            self._serve_json({'ok': True, 'palette': palette})
        elif path == '/api/delete':
            old_ver = self._state.preview_version
            self._queue.put({'type': 'delete', 'params': params})
            import time
            for _ in range(40):
                time.sleep(0.05)
                if self._state.preview_version != old_ver:
                    break
            with self._state._lock:
                palette = list(self._state.palette)
            self._serve_json({'ok': True, 'palette': palette})
        else:
            self._send_code(404)

    # ── Helpers ───────────────────────────────────────────────────────

    def _read_body(self):
        n = int(self.headers.get('Content-Length', 0))
        return self.rfile.read(n).decode('utf-8') if n else ''

    def _serve_file(self, path, ct):
        try:
            data = open(path, 'rb').read()
            self._send(200, ct, data)
        except FileNotFoundError:
            self._send_code(404)

    def _serve_bytes(self, data, ct):
        if data is None:
            self._send_code(404)
        else:
            self._send(200, ct, data)

    def _serve_json(self, obj):
        data = json.dumps(obj).encode()
        self._send(200, 'application/json', data)

    def _send(self, code, ct, data):
        self.send_response(code)
        self.send_header('Content-Type', ct)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def _send_code(self, code):
        self.send_response(code)
        self.end_headers()


# ── Server ─────────────────────────────────────────────────────────────────

class RevealServer:

    def __init__(self):
        self.state         = RevealState()
        self.command_queue = queue.Queue()
        self._httpd        = HTTPServer(('127.0.0.1', 0), _Handler)
        self._httpd.reveal_state  = self.state
        self._httpd.command_queue = self.command_queue
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, daemon=True,
        )

    @property
    def port(self):
        return self._httpd.server_address[1]

    def start(self):
        self._thread.start()

    def stop(self):
        self._httpd.shutdown()
