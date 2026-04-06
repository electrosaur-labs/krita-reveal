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
    downsample_pixels, downsample_pixels_smooth, make_original_rgb,
    make_posterized_rgb, make_solo_rgb, read_document_pixels,
    read_document_raw, run_separation,
)
from .layer_builder import build_separation_layers, compute_masks_no_despeckle, create_layers_from_masks


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
            from .suggested_color_analyzer import SuggestedColorAnalyzer
            substrate_mode = self._options.get('substrate_mode', 'none')
            palette_lab = result.get('palette_lab', [])
            suggestions = SuggestedColorAnalyzer.analyze(
                self._pixels, self._width, self._height,
                palette_lab, substrate_mode=substrate_mode,
            )
            result['_suggestions'] = suggestions
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
        if   t == 'separate':      self._do_separate(cmd['params'])
        elif t == 'solo':          self._do_solo(cmd['params'])
        elif t == 'build':         self._do_build()
        elif t == 'override':      self._do_override(cmd['params'])
        elif t == 'delete':        self._do_delete(cmd['params'])
        elif t == 'revert-delete': self._do_revert_delete(cmd['params'])
        elif t == 'rerun':         self._do_rerun(cmd['params'])
        elif t == 'push-masks':    self._do_push_masks()

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
            raw, w, h      = read_document_raw(doc)
            pixels, dw, dh = downsample_pixels_smooth(raw, w, h, max_dim=int(params.get('proxy_resolution', 800)))
        except Exception as e:
            self._state.set_error(f'Read error: {e}')
            return

        self._proxy_w, self._proxy_h = dw, dh
        self._doc_w,   self._doc_h   = w,  h
        self._proxy_pixels           = list(pixels)  # copy — preprocessing modifies in-place
        self._state.set_running(f'Separating {dw}×{dh}…')

        options = {
            # Use archetype-driven mode.  If the frontend sends an explicit
            # archetype_id (re-separate while an archetype is already selected),
            # honour it so the user's choice is preserved.  Otherwise auto-match.
            '_archetype_id':          params.get('archetype_id', '__auto__'),
            # Engine selection
            'engine_type':            params.get('engine_type', 'reveal'),
            'substrate_mode':         params.get('substrate_mode', 'none'),
            'preserve_white':         bool(params.get('preserve_white', False)),
            'preserve_black':         bool(params.get('preserve_black', False)),
            # dither_type intentionally omitted — archetype drives it (e.g. 'atkinson').
            # The UI is updated from _matched_archetype after result; reruns use _do_rerun.
            # Algorithm
            'distance_metric':        params.get('distance_metric', 'cie76'),
            'centroid_strategy':      params.get('centroid_strategy', 'ROBUST_SALIENCY'),
            'split_mode':             params.get('split_mode', 'median'),
            'color_mode':             params.get('color_mode', 'color'),
            'quantizer':              params.get('quantizer', 'wu'),
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
            # Advanced color shaping
            'neutral_sovereignty_threshold': float(params.get('neutral_sovereignty_threshold', 0)),
            'chroma_gate':            float(params.get('chroma_gate', 1.0)),
            'highlight_threshold':    float(params.get('highlight_threshold', 90)),
            'highlight_boost':        float(params.get('highlight_boost', 1.5)),
            'detail_rescue':          float(params.get('detail_rescue', 0)),
            'substrate_tolerance':    float(params.get('substrate_tolerance', 2.0)),
            # Flags
            'median_pass':            bool(params.get('median_pass', False)),
            'ignore_transparent':     bool(params.get('ignore_transparent', True)),
        }
        # Pre-smoothing: store for use after analyze_image
        preprocessing_intensity = params.get('preprocessing', 'off')
        options['_preprocessing_intensity'] = preprocessing_intensity
        # Pass colors=0 so the archetype's adaptive color count drives the first run.
        # The Colors slider is updated from _matched_archetype once the result arrives.
        self._worker = _Worker(
            pixels, dw, dh, 0, options,
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

        from pyreveal.color.encoding import lab_to_rgb
        suggestions_out = []
        for s in result.get('_suggestions', []):
            r, g, b = lab_to_rgb(s['L'], s['a'], s['b'])
            suggestions_out.append({
                'r': r, 'g': g, 'b': b,
                'hex': f"#{r:02X}{g:02X}{b:02X}",
                'reason': s.get('reason', ''),
                'score': round(s.get('score', 0), 1),
            })

        meta      = result['metadata']
        matched   = result.get('_matched_archetype', {})
        arch_name = matched.get('name', '')
        msg = f"{meta['final_colors']} colours · {arch_name} ({meta['duration']}s)" if arch_name else \
              f"{meta['final_colors']} colours ({meta['duration']}s)"

        # Only update scores from a full auto-match ranking (>1 entry).
        # Forced single-archetype reruns return a placeholder list with score=1.0
        # that must not overwrite the real ranking from the initial separation.
        fresh = result.get('_archetype_scores', [])
        if len(fresh) > 1:
            self._archetype_scores = fresh
        self._state.set_done(msg, post_jpg, orig_jpg, palette, result,
                             archetypes=self._archetype_scores,
                             matched_archetype_id=matched.get('id', ''),
                             matched_archetype=matched,
                             suggestions=suggestions_out)

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
            solo = make_solo_rgb(self._effective_assignments(), r['palette'], idx, pw, ph)
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

    # ── Delete / Revert-delete ────────────────────────────────────────

    def _delete_remap(self):
        """Return {deleted_idx: nearest_live_idx} for deleted palette entries."""
        palette     = self._result['palette']
        palette_lab = self._result.get('palette_lab', [])
        live = [i for i, c in enumerate(palette) if not c.get('is_deleted')]
        if not live or len(live) == len(palette):
            return {}

        def lab_dist(i, j):
            a, b = palette_lab[i], palette_lab[j]
            return (a['L']-b['L'])**2 + (a['a']-b['a'])**2 + (a['b']-b['b'])**2

        remap = {}
        for i, c in enumerate(palette):
            if c.get('is_deleted'):
                remap[i] = min(live, key=lambda j: lab_dist(i, j))
        return remap

    def _effective_assignments(self):
        """Return assignments with deleted palette entries remapped to nearest live color."""
        remap = self._delete_remap()
        if not remap:
            return self._result['assignments']
        return [remap.get(a, a) for a in self._result['assignments']]

    def _do_delete(self, params):
        """Badge a palette color as deleted; pixels remap to nearest live color."""
        if not self._result:
            return
        idx     = int(params.get('idx', -1))
        palette = self._result['palette']
        if idx < 0 or idx >= len(palette) or palette[idx].get('is_deleted'):
            return
        live_count = sum(1 for c in palette if not c.get('is_deleted'))
        if live_count <= 2:
            return  # keep at least 2 live colors
        palette[idx]['is_deleted'] = True
        self._rebuild_preview()

    def _do_revert_delete(self, params):
        """Restore a previously deleted palette color."""
        if not self._result:
            return
        idx     = int(params.get('idx', -1))
        palette = self._result['palette']
        if idx < 0 or idx >= len(palette):
            return
        palette[idx].pop('is_deleted', None)
        self._rebuild_preview()

    def _rebuild_preview(self):
        """Regenerate posterized JPEG and palette JSON from current result state."""
        r      = self._result
        pw, ph = r['_proxy_w'], r['_proxy_h']

        effective    = self._effective_assignments()
        r['_post_rgb'] = make_posterized_rgb(effective, r['palette'], pw, ph)
        post_jpg     = _to_jpeg(r['_post_rgb'], pw, ph)

        total  = pw * ph
        counts = [0] * len(r['palette'])
        for a in effective:
            if a < len(counts):
                counts[a] += 1
        r['_coverage'] = [100.0 * c / total for c in counts]

        # Count how many deleted colours map to each live colour
        remap = self._delete_remap()
        merge_counts = [0] * len(r['palette'])
        for target in remap.values():
            merge_counts[target] += 1

        coverage    = r['_coverage']
        palette_out = [
            {
                'r': c['r'], 'g': c['g'], 'b': c['b'],
                'hex': f"#{c['r']:02X}{c['g']:02X}{c['b']:02X}",
                'pct': 0.0 if c.get('is_deleted') else round(coverage[i] if i < len(coverage) else 0.0, 1),
                'is_deleted': bool(c.get('is_deleted', False)),
                'merge_count': merge_counts[i],
            }
            for i, c in enumerate(r['palette'])
        ]
        self._state.set_preview_and_palette(post_jpg, palette_out)

    # ── Rerun with archetype ──────────────────────────────────────────

    def _do_rerun(self, params):
        if self._proxy_pixels is None:
            return
        archetype_id = params.get('archetype_id', '__auto__')
        options = {'_archetype_id': archetype_id}
        # Only override mechanical knobs when the UI explicitly sends them.
        # Archetype-switch reruns omit these so the archetype's own values drive.
        if 'density' in params:
            options['density_floor']  = float(params['density']) / 100.0
        if 'speckle' in params:
            options['speckle_rescue'] = int(params['speckle'])
        if 'clamp' in params:
            options['shadow_clamp']   = int(params['clamp'])
        # Advanced algorithm overrides — pass when explicitly sent (scheduleRerun path)
        for key in ('vibrancy_boost', 'l_weight', 'c_weight', 'black_bias',
                    'shadow_point', 'palette_reduction', 'hue_lock_angle',
                    'neutral_sovereignty_threshold', 'chroma_gate',
                    'highlight_threshold', 'highlight_boost',
                    'detail_rescue', 'substrate_tolerance'):
            if key in params:
                options[key] = float(params[key])
        for key in ('vibrancy_mode', 'substrate_mode',
                    'engine_type', 'color_mode', 'dither_type',
                    'distance_metric', 'centroid_strategy', 'split_mode', 'quantizer'):
            if key in params:
                options[key] = str(params[key])
        for key in ('enable_palette_reduction', 'enable_hue_gap_analysis',
                    'preserve_white', 'preserve_black',
                    'median_pass', 'ignore_transparent'):
            if key in params:
                options[key] = bool(params[key])
        for key in ('mesh_size', 'trap_size'):
            if key in params:
                options[key] = int(params[key])
        if 'preprocessing' in params:
            options['_preprocessing_intensity'] = str(params['preprocessing'])
        dw, dh = self._proxy_w, self._proxy_h
        self._state.set_running(f'Applying archetype…')
        # colors=0 when omitted → archetype's adaptive count drives (same as _do_separate).
        # Manual slider reruns send colors explicitly so the user's value is respected.
        colors = int(params['colors']) if 'colors' in params else 0
        self._worker = _Worker(
            self._proxy_pixels, dw, dh, colors, options,
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
        from PyQt5.QtCore import Qt
        from PyQt5.QtWidgets import QApplication
        self._state.set_running('Building layers…')
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            def _progress(msg):
                self._state.set_message(msg)
                QApplication.processEvents()

            masks, palette_rgb, palette_lab, speckle = \
                compute_masks_no_despeckle(doc, self._result, on_progress=_progress)

            # Store masks + metadata; Chrome JS will despeckle and POST back
            width, height = doc.width(), doc.height()
            masks_bytes = b''.join(bytes(m) for m in masks)
            self._build_palette_rgb = palette_rgb
            self._build_palette_lab = palette_lab
            self._state.set_despeckle_ready(masks_bytes, {
                'width':  width,
                'height': height,
                'total':  len(masks),
                'speckle_threshold': speckle,
            })
        except Exception as e:
            self._state.set_build_done(f'Build error: {e}', is_error=True)
        finally:
            QApplication.restoreOverrideCursor()

    def _do_push_masks(self):
        """Receive despeckled masks from Chrome and create Krita layers."""
        with self._state._lock:
            masks = self._state.despeckled_masks
        if not masks:
            self._state.set_error('No masks received.')
            return
        doc = Krita.instance().activeDocument()
        if not doc:
            self._state.set_message('No active document.', is_error=True)
            return
        from PyQt5.QtCore import Qt
        from PyQt5.QtWidgets import QApplication
        self._state.set_running('Creating layers…')
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            def _progress(msg):
                self._state.set_message(msg)
                QApplication.processEvents()

            def _on_ready():
                self._state.set_close_window()
                QApplication.processEvents()

            n = create_layers_from_masks(
                doc, self._build_palette_rgb, self._build_palette_lab, masks,
                on_progress=_progress, on_ready=_on_ready)
            self._state.set_build_done(f'Created {n} layers.')
        except Exception as e:
            self._state.set_build_done(f'Layer error: {e}', is_error=True)
        finally:
            QApplication.restoreOverrideCursor()
