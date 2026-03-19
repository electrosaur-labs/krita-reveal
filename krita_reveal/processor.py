"""
processor.py — RevealCommandProcessor: drains the command queue in the Qt main thread.

Krita's API must only be called from the Qt main thread. This QObject sits in
that thread, polls the command queue every 100 ms, and executes Krita API calls.
"""

from __future__ import annotations

import queue

from PyQt5.QtCore import (
    QBuffer, QByteArray, QIODevice, QObject, QThread, QTimer, pyqtSignal,
)
from PyQt5.QtGui import QImage

from .pipeline import (
    downsample_pixels, make_original_rgb, make_posterized_rgb,
    make_solo_rgb, read_document_pixels, run_separation,
)
from .layer_builder import build_separation_layers


# ── JPEG helper ────────────────────────────────────────────────────────────

def _to_jpeg(rgb_bytes: bytes, width: int, height: int, quality: int = 85) -> bytes:
    img = QImage(rgb_bytes, width, height, width * 3, QImage.Format_RGB888).copy()
    buf = QByteArray()
    qbuf = QBuffer(buf)
    qbuf.open(QIODevice.WriteOnly)
    img.save(qbuf, 'JPEG', quality)
    return bytes(buf)


# ── Worker thread ──────────────────────────────────────────────────────────

class _Worker(QThread):
    done  = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, pixels, width, height, target_colors, options):
        super().__init__()
        self._pixels        = pixels
        self._width         = width
        self._height        = height
        self._target_colors = target_colors
        self._options       = options

    def run(self):
        try:
            orig_rgb = make_original_rgb(self._pixels, self._width, self._height)
            result   = run_separation(
                self._pixels, self._width, self._height,
                self._target_colors, self._options,
            )
            result['_orig_rgb'] = orig_rgb
            result['_post_rgb'] = make_posterized_rgb(
                result['assignments'], result['palette'],
                self._width, self._height,
            )
            n      = len(result['palette'])
            total  = self._width * self._height
            counts = [0] * n
            for idx in result['assignments']:
                if idx < n:
                    counts[idx] += 1
            result['_coverage'] = [100.0 * c / total for c in counts]
            self.done.emit(result)
        except Exception as e:
            self.error.emit(str(e))


# ── Command processor ──────────────────────────────────────────────────────

class RevealCommandProcessor(QObject):

    def __init__(self, server, parent=None):
        super().__init__(parent)
        self._server  = server
        self._state   = server.state
        self._queue   = server.command_queue
        self._worker           = None
        self._result           = None
        self._proxy_pixels     = None   # stored for archetype rerun
        self._archetype_scores = []     # full ranking from initial DNA — reused on reruns
        self._proxy_w = self._proxy_h = 0
        self._doc_w   = self._doc_h   = 0

        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._drain)
        self._timer.start()

    def _drain(self):
        try:
            while True:
                cmd = self._queue.get_nowait()
                self._execute(cmd)
        except queue.Empty:
            pass

    def _execute(self, cmd):
        t = cmd['type']
        if   t == 'separate': self._do_separate(cmd['params'])
        elif t == 'solo':     self._do_solo(cmd['params'])
        elif t == 'build':    self._do_build()
        elif t == 'override': self._do_override(cmd['params'])
        elif t == 'delete':   self._do_delete(cmd['params'])
        elif t == 'rerun':    self._do_rerun(cmd['params'])

    # ── Separate ──────────────────────────────────────────────────────

    def _do_separate(self, params):
        app = Krita.instance()
        doc = app.activeDocument()
        if not doc:
            self._state.set_error('No active document.')
            return
        if doc.colorModel() != 'LABA':
            self._state.set_error('Document must be in Lab colour mode.')
            return

        self._state.set_running('Reading pixels…')
        try:
            pixels, w, h   = read_document_pixels(doc)
            pixels, dw, dh = downsample_pixels(pixels, w, h, max_dim=800)
        except Exception as e:
            self._state.set_error(f'Read error: {e}')
            return

        self._proxy_w, self._proxy_h = dw, dh
        self._doc_w,   self._doc_h   = w,  h
        self._proxy_pixels           = list(pixels)  # copy — preprocessing modifies in-place
        self._state.set_running(f'Separating {dw}×{dh}…')

        options = {
            # Use archetype-driven mode (auto-match); mechanical knobs override
            '_archetype_id':          '__auto__',
            # Core mechanical knobs
            'density_floor':          params.get('density', 0.5) / 100.0,
            'speckle_rescue':         int(params.get('speckle', 0)),
            'shadow_clamp':           int(params.get('clamp', 0)),
            # Engine selection
            'engine_type':            params.get('engine_type', 'reveal'),
            'substrate_mode':         params.get('substrate_mode', 'none'),
            'preserve_white':         bool(params.get('preserve_white', False)),
            'preserve_black':         bool(params.get('preserve_black', False)),
            # Output
            'dither_type':            params.get('dither_type', 'none'),
            # Algorithm
            'distance_metric':        params.get('distance_metric', 'cie76'),
            'strategy':               params.get('strategy', 'ROBUST_SALIENCY'),
            'split_mode':             params.get('split_mode', 'median'),
            # Saturation
            'vibrancy_boost':         float(params.get('vibrancy_boost', 1.4)),
            'vibrancy_mode':          params.get('vibrancy_mode', 'aggressive'),
            # Color merging
            'palette_reduction':      float(params.get('palette_reduction', 6.0)),
            'enable_palette_reduction': bool(params.get('enable_palette_reduction', True)),
            'enable_hue_gap_analysis':  bool(params.get('enable_hue_gap_analysis', True)),
            'hue_lock_angle':         float(params.get('hue_lock_angle', 20)),
            # Color priority
            'l_weight':               float(params.get('l_weight', 1.2)),
            'c_weight':               float(params.get('c_weight', 2.0)),
            'black_bias':             float(params.get('black_bias', 3.0)),
            # Tone
            'shadow_point':           float(params.get('shadow_point', 15)),
        }
        # Pre-smoothing: store for use after analyze_image
        preprocessing_intensity = params.get('preprocessing', 'off')
        options['_preprocessing_intensity'] = preprocessing_intensity
        self._worker = _Worker(
            pixels, dw, dh, int(params.get('colors', 6)), options,
        )
        self._worker.done.connect(self._on_worker_done)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

    def _on_worker_done(self, result):
        result['_proxy_w'] = self._proxy_w
        result['_proxy_h'] = self._proxy_h
        result['_doc_w']   = self._doc_w
        result['_doc_h']   = self._doc_h
        self._result = result

        pw, ph   = self._proxy_w, self._proxy_h
        post_jpg = _to_jpeg(result['_post_rgb'], pw, ph)
        orig_jpg = _to_jpeg(result['_orig_rgb'], pw, ph)

        coverage = result.get('_coverage', [])
        palette  = [
            {
                'r': c['r'], 'g': c['g'], 'b': c['b'],
                'hex': f"#{c['r']:02X}{c['g']:02X}{c['b']:02X}",
                'pct': round(coverage[i] if i < len(coverage) else 0.0, 1),
            }
            for i, c in enumerate(result['palette'])
        ]

        meta      = result['metadata']
        matched   = result.get('_matched_archetype', {})
        arch_name = matched.get('name', '')
        msg = f"{meta['final_colors']} colours · {arch_name} ({meta['duration']}s)" if arch_name else \
              f"{meta['final_colors']} colours ({meta['duration']}s)"

        # Store scores from first separation; reuse on reruns (same DNA, scores don't change)
        fresh = result.get('_archetype_scores', [])
        if fresh:
            self._archetype_scores = fresh
        self._state.set_done(msg, post_jpg, orig_jpg, palette, result,
                             archetypes=self._archetype_scores,
                             matched_archetype_id=matched.get('id', ''))

    def _on_worker_error(self, msg):
        self._state.set_error(f'Error: {msg}')

    # ── Solo ──────────────────────────────────────────────────────────

    def _do_solo(self, params):
        if not self._result:
            return
        r   = self._result
        idx = int(params.get('idx', -1))
        pw, ph = r['_proxy_w'], r['_proxy_h']
        if idx < 0:
            jpg = _to_jpeg(r['_post_rgb'], pw, ph)
        else:
            solo = make_solo_rgb(r['assignments'], r['palette'], idx, pw, ph)
            jpg  = _to_jpeg(solo, pw, ph)
        self._state.set_preview(jpg)

    # ── Override ──────────────────────────────────────────────────────

    def _do_override(self, params):
        """Replace one palette entry with a user-chosen color and refresh preview."""
        if not self._result:
            return
        idx = int(params.get('idx', -1))
        r   = int(params.get('r', 0))
        g   = int(params.get('g', 0))
        b   = int(params.get('b', 0))
        if idx < 0 or idx >= len(self._result['palette']):
            return

        self._result['palette'][idx] = {'r': r, 'g': g, 'b': b}
        self._rebuild_preview()

    # ── Delete ────────────────────────────────────────────────────────

    def _do_delete(self, params):
        """Merge a palette color into its nearest neighbor and refresh preview."""
        if not self._result:
            return
        idx = int(params.get('idx', -1))
        palette = self._result['palette']
        n = len(palette)
        if idx < 0 or idx >= n or n <= 2:
            return

        # Find nearest neighbor by RGB distance
        def rgb_dist(a, b):
            return (a['r'] - b['r'])**2 + (a['g'] - b['g'])**2 + (a['b'] - b['b'])**2

        target   = palette[idx]
        best_i   = -1
        best_d   = float('inf')
        for i, c in enumerate(palette):
            if i == idx:
                continue
            d = rgb_dist(target, c)
            if d < best_d:
                best_d, best_i = d, i

        # Remap assignments: idx → best_i, then collapse indices above idx
        assignments = self._result['assignments']
        for i in range(len(assignments)):
            if assignments[i] == idx:
                assignments[i] = best_i
            elif assignments[i] > idx:
                assignments[i] -= 1

        # Remove color from palette
        palette.pop(idx)

        # Recalculate coverage
        total  = self._proxy_w * self._proxy_h
        counts = [0] * len(palette)
        for a in assignments:
            if a < len(palette):
                counts[a] += 1
        self._result['_coverage'] = [100.0 * c / total for c in counts]

        self._rebuild_preview()

    def _rebuild_preview(self):
        """Regenerate posterized JPEG and palette JSON from current result state."""
        r      = self._result
        pw, ph = r['_proxy_w'], r['_proxy_h']

        r['_post_rgb'] = make_posterized_rgb(r['assignments'], r['palette'], pw, ph)
        post_jpg = _to_jpeg(r['_post_rgb'], pw, ph)

        coverage = r.get('_coverage', [])
        palette_out = [
            {
                'r': c['r'], 'g': c['g'], 'b': c['b'],
                'hex': f"#{c['r']:02X}{c['g']:02X}{c['b']:02X}",
                'pct': round(coverage[i] if i < len(coverage) else 0.0, 1),
            }
            for i, c in enumerate(r['palette'])
        ]
        self._state.set_preview_and_palette(post_jpg, palette_out)

    # ── Rerun with archetype ──────────────────────────────────────────

    def _do_rerun(self, params):
        if self._proxy_pixels is None:
            return
        archetype_id = params.get('archetype_id', '__auto__')
        options = {
            '_archetype_id':  archetype_id,
            'density_floor':  params.get('density', 0.5) / 100.0,
            'speckle_rescue': int(params.get('speckle', 0)),
            'shadow_clamp':   int(params.get('clamp', 0)),
        }
        dw, dh = self._proxy_w, self._proxy_h
        self._state.set_running(f'Applying archetype…')
        self._worker = _Worker(
            self._proxy_pixels, dw, dh, int(params.get('colors', 6)), options,
        )
        self._worker.done.connect(self._on_worker_done)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

    # ── Build ─────────────────────────────────────────────────────────

    def _do_build(self):
        if not self._result:
            return
        doc = Krita.instance().activeDocument()
        if not doc:
            self._state.set_message('No active document.', is_error=True)
            return
        try:
            n = build_separation_layers(doc, self._result)
            self._state.set_message(f'Created {n} layers.')
        except Exception as e:
            self._state.set_message(f'Layer error: {e}', is_error=True)
