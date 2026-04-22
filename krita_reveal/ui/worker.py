"""
worker.py — Background worker thread for color separation.
"""

from __future__ import annotations
from PyQt5.QtCore import QThread, pyqtSignal as Signal

from ..pipeline import make_original_rgb, make_posterized_rgb, run_separation
from ..suggested_color_analyzer import SuggestedColorAnalyzer


class _Worker(QThread):
    done = Signal(dict)
    error = Signal(str)
    status = Signal(str, int) # message, progress_pct

    def __init__(self, pixels, width, height, target_colors, options):
        super().__init__()
        self._pixels = pixels
        self._width = width
        self._height = height
        self._target_colors = target_colors
        self._options = options

    def run(self):
        try:
            import time
            start = time.perf_counter()
            from ..constants import log
            log(f"Worker: Starting {self._width}x{self._height}...")

            def _on_prog(msg, pct):
                self.status.emit(msg, pct)

            orig_rgb = make_original_rgb(self._pixels, self._width, self._height)
            result = run_separation(self._pixels, self._width, self._height, self._target_colors, self._options, on_progress=_on_prog)
            result['_orig_rgb'] = orig_rgb
            _on_prog("Generating Preview", 90)
            result['_post_rgb'] = make_posterized_rgb(result['assignments'], result['palette'], self._width, self._height)

            try:
                import numpy as np
                idx = np.asarray(result['assignments'])
                counts = np.bincount(idx.astype(np.intp), minlength=len(result['palette']))
            except ImportError:
                n = len(result['palette'])
                counts = [0] * n
                for idx in result['assignments']:
                    if idx < n:
                        counts[idx] += 1

            total = self._width * self._height
            result['_coverage'] = [100.0 * c / total for c in counts]
            result['_suggestions'] = SuggestedColorAnalyzer.analyze(
                self._pixels, self._width, self._height,
                result.get('palette_lab', []),
                substrate_mode=self._options.get('substrate_mode', 'none')
            )
            elapsed = time.perf_counter() - start
            log(f"Worker: Success in {elapsed:.2f}s")
            self.done.emit(result)
        except Exception as e:
            import traceback
            from ..constants import log
            log(f"Worker ERROR: {e}\n{traceback.format_exc()}")
            self.error.emit(str(e))
