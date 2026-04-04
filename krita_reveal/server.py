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
        self.close_window        = False    # signals JS to call window.close()
        # Despeckle offload: Chrome JS despeckles masks, Python creates layers
        self.despeckle_masks     = None     # bytes — concatenated undespeckled masks
        self.despeckle_params    = None     # dict — {width, height, speckle_threshold, total}
        self.despeckled_masks    = None     # list of bytearray — from Chrome

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

    def set_close_window(self):
        """Signal the JS to close the Chrome window (layers are about to be pushed)."""
        with self._lock:
            self.close_window = True

    def set_despeckle_ready(self, masks_bytes, params):
        """Store undespeckled masks and signal Chrome to despeckle them."""
        with self._lock:
            self.status           = 'despeckle'
            self.message          = 'Despeckle (JS)…'
            self.is_error         = False
            self.despeckle_masks  = masks_bytes
            self.despeckle_params = params

    def set_build_done(self, msg, is_error=False):
        """Transition back to 'done' after a build completes (or fails)."""
        with self._lock:
            self.status           = 'done'
            self.message          = msg
            self.is_error         = is_error
            self.close_window     = False
            self.despeckle_masks  = None
            self.despeckle_params = None
            self.despeckled_masks = None

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
                'close_window':         self.close_window,
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
        elif path == '/api/despeckle-params':
            with self._state._lock:
                p = self._state.despeckle_params
            self._serve_json(p) if p else self._send_code(404)
        elif path == '/data/masks':
            with self._state._lock:
                d = self._state.despeckle_masks
            self._serve_bytes(d, 'application/octet-stream') if d else self._send_code(404)
        else:
            self._send_code(404)

    def do_POST(self):
        path = self.path.split('?')[0]

        # Binary endpoint — read raw bytes, not UTF-8
        if path == '/api/push-masks':
            n = int(self.headers.get('Content-Length', 0))
            raw = self.rfile.read(n) if n else b''
            with self._state._lock:
                p = self._state.despeckle_params
            if not p:
                self._send_code(400)
                return
            mask_size = p['width'] * p['height']
            masks = [bytearray(raw[i * mask_size:(i + 1) * mask_size])
                     for i in range(p['total'])]
            with self._state._lock:
                self._state.despeckled_masks = masks
                # Set running immediately so poll doesn't re-trigger despeckle
                self._state.status  = 'running'
                self._state.message = 'Creating layers…'
            self._queue.put({'type': 'push-masks'})
            self._serve_json({'ok': True})
            return

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
        elif path == '/api/debug-compare':
            self._queue.put({'type': 'debug-compare'})
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
        elif path == '/api/revert-delete':
            old_ver = self._state.preview_version
            self._queue.put({'type': 'revert-delete', 'params': params})
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
