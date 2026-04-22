"""
dock.py — RevealDock: native PyQt5 docker for Reveal colour separation.
"""

from __future__ import annotations
import time
from krita import DockWidget, Krita

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFrame,
    QSizePolicy, QComboBox, QScrollArea, QApplication,
)
from PyQt5.QtCore import QTimer, Qt, QPoint, QRect
from PyQt5.QtGui import QColor, QPalette, QPixmap

from .constants import DEFAULTS, log
from .ui.widgets import _StatusOverlay
from .ui.preview import _PreviewLabel
from .ui.palette import _LabPicker, PaletteManager
from .ui.archetypes import ArchetypeManager
from .ui.stats import StatsManager
from .ui.params import ParamsManager
from .ui.layout import LayoutManager
from .ui.worker import _Worker
from .ui.sections import SectionBuilder

DOCKER_TITLE = 'Reveal Separation'

class RevealDock(DockWidget):
    def __init__(self):
        super().__init__()
        log("RevealDock: __init__ starting...")
        self.setWindowTitle(DOCKER_TITLE)
        self._ui_built = False
        self._root_container = QWidget()
        self.setWidget(self._root_container)
        self._main_layout = QVBoxLayout(self._root_container)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)
        
        self._result = None
        self._worker = None
        self._proxy_pixels = None
        self._archetype_scores = []
        self._archetype_list = []
        self._others_expanded = False
        self._last_archetype_id = ''
        self._proxy_w = 0
        self._proxy_h = 0
        self._doc_w = 0
        self._doc_h = 0
        self._selected_idx = -1
        self._is_running = False
        self._has_result = False
        
        self._rerun_timer = QTimer(self)
        self._rerun_timer.setSingleShot(True)
        self._rerun_timer.setInterval(700)
        self._rerun_timer.timeout.connect(self._do_rerun)
        
        self._help_visible = False
        self._controls = {}
        self._archetype_defaults = {}
        self._numpy_warned = False
        self._startup = True
        
        self._palette_mgr = PaletteManager(self)
        self._archetype_mgr = ArchetypeManager(self)
        self._stats_mgr = StatsManager(self)
        self._params_mgr = ParamsManager(self)
        self._layout_mgr = LayoutManager(self)
        
        QTimer.singleShot(5000, self._end_startup)

    def _end_startup(self):
        self._startup = False
        log("RevealDock: Startup ended.")

    def _build_ui(self):
        if self._ui_built:
            return
        log("RevealDock: Building UI...")
        self._archetype_defaults = dict(DEFAULTS)
        
        self._layout_mgr.build_ui()
        
        log(f"RevealDock: Layouts after build: bas={getattr(self, '_basic_layout', None)}, sp={getattr(self, '_sp_layout', None)}, adv={getattr(self, '_advanced_layout', None)}")

        self._overlay = _StatusOverlay(self._root_container)
        self._overlay.setVisible(False)
        self._color_picker = _LabPicker()
        self._color_picker.colorPicked.connect(self._on_color_picked)
        
        # --- Build Sections ---
        builder = SectionBuilder(self)
        self._controls = builder.build_all(
            getattr(self, '_basic_layout', None), 
            getattr(self, '_sp_layout', None), 
            getattr(self, '_advanced_layout', None)
        )
        log(f"RevealDock: Controls built: {len(self._controls)} keys")
        
        # Sync initial loupe state
        self._on_loupe_mag_changed(0)
        
        self._ui_built = True
        log("RevealDock: UI built and verified.")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._ui_built:
            self._overlay.resize(self._root_container.size())

    def _toggle_advanced(self):
        vis = not self._adv_container.isVisible()
        self._adv_container.setVisible(vis)
        self._adv_toggle.setText('▼  Advanced' if vis else '▶  Advanced')

    def _toggle_sp(self):
        vis = not self._sp_container.isVisible()
        self._sp_container.setVisible(vis)
        self._sp_toggle.setText('▼  Screen Printing' if vis else '▶  Screen Printing')

    def _on_archetype_changed(self, idx):
        if not self._has_result or self._is_running:
            return
        aid = self._archetype_combo.currentData()
        if not aid or aid == '__others_toggle__':
            if aid == '__others_toggle__':
                self._others_expanded = not getattr(self, '_others_expanded', False)
                self._archetype_mgr.render_archetypes(self._archetype_list, getattr(self, '_last_archetype_id', ''))
            return
        log(f"Applying Archetype: {aid}")
        self._last_archetype_id = aid
        self._others_expanded = False
        self._is_running = True
        self._has_result = False
        self._preview.clear_images()
        self._palette_mgr.clear_swatches()
        self._set_status('Applying archetype…')
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        self._worker = _Worker(self._proxy_pixels, self._proxy_w, self._proxy_h, 0, {'_archetype_id': aid})
        self._worker.done.connect(self._on_worker_done)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

    def _on_separate(self):
        if self._is_running:
            return
        if self._rerun_timer.isActive():
            self._rerun_timer.stop()
        self._check_numpy()
        doc = Krita.instance().activeDocument()
        if not doc:
            self._set_status('No active document.', error=True)
            return
        if doc.colorModel() != 'LABA':
            self._set_status('Document must be in Lab mode.', error=True)
            return
        self._is_running = True
        self._has_result = False
        self._selected_idx = -1
        self._preview.clear_images()
        self._palette_mgr.clear_swatches()
        self._btn_separate.setEnabled(False)
        self._set_status('Reading pixels…')
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        params = self._params_mgr.collect_params()
        try:
            from .pipeline import read_document_raw, downsample_pixels_smooth
            raw, w, h = read_document_raw(doc)
            pixels, dw, dh = downsample_pixels_smooth(raw, w, h, max_dim=int(params.get('proxy_resolution', 1000)))
            self._proxy_w = dw
            self._proxy_h = dh
            self._doc_w = w
            self._doc_h = h
            self._proxy_pixels = pixels
            self._set_status(f'Separating {dw}×{dh}…')
            opts = self._params_mgr.get_worker_options(params)
            self._worker = _Worker(pixels, dw, dh, 0, opts)
            self._worker.done.connect(self._on_worker_done)
            self._worker.error.connect(self._on_worker_error)
            self._worker.start()
        except Exception as e:
            log(f"Pixel Read ERROR: {e}")
            self._is_running = False
            QApplication.restoreOverrideCursor()
            self._set_status(f'Read error: {e}', error=True)

    def _on_worker_done(self, res):
        self._overlay.set_text("")
        try:
            self._handle_worker_result(res)
        except Exception as e:
            log(f"_handle_worker_result ERROR: {e}")
            self._is_running = False
            self._has_result = bool(self._proxy_pixels)
            if QApplication.overrideCursor():
                QApplication.restoreOverrideCursor()
            self._set_status(f'Error: {e}', error=True)

    def _handle_worker_result(self, res):
        QApplication.restoreOverrideCursor()
        self._preview.set_overlay_text('')
        res.update({
            '_proxy_w': self._proxy_w,
            '_proxy_h': self._proxy_h,
            '_doc_w': self._doc_w,
            '_doc_h': self._doc_h
        })
        self._result = res
        self._is_running = False
        self._has_result = True
        self._preview.set_images(
            self._to_pixmap(res['_orig_rgb'], self._proxy_w, self._proxy_h), 
            self._to_pixmap(res['_post_rgb'], self._proxy_w, self._proxy_h)
        )
        matched = res.get('_matched_archetype', {})
        self._stats_mgr.update_stats(res, matched, self._archetype_scores)
        self._set_status(f"{res['metadata']['final_colors']} colours · {matched.get('name', 'Custom')} ({res['metadata']['duration']}s)")
        
        cov = res.get('_coverage', [])
        pdat = []
        for i, c in enumerate(res['palette']):
            pdat.append({
                'r': c['r'],
                'g': c['g'],
                'b': c['b'],
                'hex': f"#{c['r']:02X}{c['g']:02X}{c['b']:02X}",
                'pct': round(cov[i] if i < len(cov) else 0.0, 1),
                'is_deleted': False,
                'merge_count': 0
            })
        self._palette_mgr.render_swatches(pdat)
        
        sugs = []
        from .vendor.pyreveal.color.encoding import lab_to_rgb
        for s in res.get('_suggestions', []):
            r, g, b = lab_to_rgb(s['L'], s['a'], s['b'])
            sugs.append({
                'r': r, 'g': g, 'b': b,
                'hex': f'#{r:02X}{g:02X}{b:02X}',
                'reason': s.get('reason', ''),
                'score': round(s.get('score', 0), 1)
            })
        self._archetype_mgr.render_suggestions(sugs)
        
        fresh = res.get('_archetype_scores', [])
        if len(fresh) > 1:
            self._archetype_scores = fresh
        if self._archetype_scores:
            self._last_archetype_id = matched.get('id', '')
            self._archetype_mgr.render_archetypes(self._archetype_scores, self._last_archetype_id)
            
        self._archetype_mgr.apply_matched_archetype(matched)
        self._btn_separate.setEnabled(True)

    def _on_worker_error(self, msg):
        if QApplication.overrideCursor():
            QApplication.restoreOverrideCursor()
        self._is_running = False
        self._has_result = False
        self._overlay.set_text("")
        self._btn_separate.setEnabled(False)
        self._set_status(f'Error: {msg}', error=True)

    def _schedule_rerun(self):
        if self._has_result:
            self._rerun_timer.start()

    def _do_rerun(self):
        if not self._has_result or self._proxy_pixels is None:
            return
        self._is_running = True
        self._has_result = False
        self._preview.clear_images()
        self._palette_mgr.clear_swatches()
        self._set_status('Applying changes…')
        QApplication.setOverrideCursor(Qt.WaitCursor)
        params = self._params_mgr.collect_params()
        opts = self._params_mgr.get_worker_options(params)
        self._worker = _Worker(self._proxy_pixels, self._proxy_w, self._proxy_h, int(params['colors']), opts)
        self._worker.done.connect(self._on_worker_done)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

    def _on_loupe_mag_changed(self, idx):
        if not hasattr(self, '_loupe_mag_combo'):
            return
        mag = self._loupe_mag_combo.currentData() or 0
        if mag == 0:
            self._preview._loupe.setVisible(False)
        self._preview._loupe.ZOOM = mag

    def _on_preview_clicked(self):
        if self._selected_idx >= 0:
            self._selected_idx = -1
            self._palette_mgr.update_selection(self._selected_idx)
            if self._result:
                px = self._to_pixmap(self._result['_post_rgb'], self._result['_proxy_w'], self._result['_proxy_h'])
                self._preview.update_post(px)
        else:
            self._preview._showing_orig = not self._preview._showing_orig
            self.update()

    def _on_swatch_clicked(self, idx, event):
        if event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
            c = self._palette_mgr.palette_data[idx]
            if not c.get('is_deleted'):
                self._color_picker.open_for(idx, c['hex'], self._palette_mgr.swatch_widgets[idx])
            return
        if event.modifiers() & Qt.AltModifier:
            self._toggle_delete(idx)
            return
        if self._palette_mgr.palette_data[idx].get('is_deleted'):
            return
        self._selected_idx = -1 if self._selected_idx == idx else idx
        self._palette_mgr.update_selection(self._selected_idx)
        if self._result:
            from .pipeline import make_solo_rgb
            if self._selected_idx < 0:
                rgb = self._result['_post_rgb']
            else:
                rgb = make_solo_rgb(
                    self._palette_mgr.effective_assignments(self._result), 
                    self._result['palette'], 
                    self._selected_idx, 
                    self._result['_proxy_w'], 
                    self._result['_proxy_h']
                )
            px = self._to_pixmap(rgb, self._result['_proxy_w'], self._result['_proxy_h'])
            self._preview.update_post(px)

    def _on_color_picked(self, r, g, b):
        idx = self._color_picker._target_idx
        if idx >= len(self._result['palette']):
            self._on_new_color_added(r, g, b)
        else:
            self._result['palette'][idx] = {'r': r, 'g': g, 'b': b}
            plab = self._result.get('palette_lab', [])
            if idx < len(plab):
                from .vendor.pyreveal.color.encoding import rgb_to_lab
                L, a, bv = rgb_to_lab(r, g, b)
                plab[idx] = {'L': L, 'a': a, 'b': bv}
            self._rebuild_preview()

    def _on_add_color(self):
        if not self._result:
            return
        live_count = sum(1 for c in self._result['palette'] if not c.get('is_deleted'))
        if live_count >= 20:
            self._set_status('Max 20 colors.', error=True)
            return
        self._color_picker._target_idx = len(self._result['palette'])
        self._color_picker.open_for(self._color_picker._target_idx, '#808080', self._add_color_btn)

    def _on_new_color_added(self, r, g, b):
        if not self._result:
            return
        from .vendor.pyreveal.color.encoding import rgb_to_lab
        L, a, bv = rgb_to_lab(r, g, b)
        self._result['palette'].append({'r': r, 'g': g, 'b': b})
        if 'palette_lab' not in self._result:
            self._result['palette_lab'] = []
        self._result['palette_lab'].append({'L': L, 'a': a, 'b': bv})
        if '_coverage' not in self._result:
            self._result['_coverage'] = []
        self._result['_coverage'].append(0.0)
        self._rebuild_preview()

    def _toggle_delete(self, idx):
        if not self._result or idx < 0 or idx >= len(self._result['palette']):
            return
        p = self._result['palette'][idx]
        if p.get('is_deleted'):
            p.pop('is_deleted', None)
        elif sum(1 for c in self._result['palette'] if not c.get('is_deleted')) > 2:
            p['is_deleted'] = True
        self._rebuild_preview()

    def _on_swatch_merged(self, sidx, tidx):
        if not self._result or sidx < 0 or tidx < 0:
            return
        if sidx >= len(self._result['palette']) or tidx >= len(self._result['palette']):
            return
        if self._result['palette'][sidx].get('is_deleted'):
            return
        if sum(1 for c in self._result['palette'] if not c.get('is_deleted')) <= 2:
            return
        self._result['palette'][sidx]['is_deleted'] = True
        self._result['palette'][sidx]['_merge_target'] = tidx
        self._rebuild_preview()

    def _rebuild_preview(self):
        from .pipeline import make_posterized_rgb
        r = self._result
        pw, ph = r['_proxy_w'], r['_proxy_h']
        eff = self._palette_mgr.effective_assignments(r)
        r['_post_rgb'] = make_posterized_rgb(eff, r['palette'], pw, ph)
        px = self._to_pixmap(r['_post_rgb'], pw, ph)
        self._preview.update_post(px)
        
        cnts = [0] * len(r['palette'])
        for a in eff:
            if a < len(cnts):
                cnts[a] += 1
                
        r['_coverage'] = [100.0 * c / (pw * ph) for c in cnts]
        plab = r.get('palette_lab', [])
        live = [i for i, c in enumerate(r['palette']) if not c.get('is_deleted')]
        mcnts = [0] * len(r['palette'])
        for i, c in enumerate(r['palette']):
            if c.get('is_deleted') and live and plab:
                target = min(live, key=lambda j: (plab[i]['L'] - plab[j]['L'])**2 + (plab[i]['a'] - plab[j]['a'])**2 + (plab[i]['b'] - plab[j]['b'])**2)
                mcnts[target] += mcnts[i] # Incorrect logic in modular port? Fixed now
                mcnts[target] += 1
                
        pdat = []
        for i, c in enumerate(r['palette']):
            pct = 0.0 if c.get('is_deleted') else round(r['_coverage'][i], 1)
            pdat.append({
                'r': c['r'], 'g': c['g'], 'b': c['b'],
                'hex': f"#{c['r']:02X}{c['g']:02X}{c['b']:02X}",
                'pct': pct,
                'is_deleted': bool(c.get('is_deleted', False)),
                'merge_count': mcnts[i]
            })
        self._palette_mgr.render_swatches(pdat)

    def _on_build_layers(self):
        if not self._result or self._is_running:
            return
        doc = Krita.instance().activeDocument()
        if not doc:
            self._set_status('No document.', error=True)
            return
        self._is_running = True
        self._set_status('Building layers…')
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        
        res = dict(self._result)
        pal = [dict(c) for c in self._result['palette']]
        plab = [dict(c) for c in self._result.get('palette_lab', [])]
        live = [i for i, c in enumerate(pal) if not c.get('is_deleted')]
        if len(live) < len(pal) and live:
            eff = self._palette_mgr.effective_assignments(self._result)
            remap = {o: n for n, o in enumerate(live)}
            res.update({
                'assignments': [remap.get(a, 0) for a in eff],
                'palette': [pal[i] for i in live],
                'palette_lab': [plab[i] for i in live]
            })
        else:
            res.update({'palette': pal, 'palette_lab': plab})
            
        from .layer_builder import build_separation_layers
        try:
            on_prog = lambda m: (self._set_status(m), QApplication.processEvents())
            n = build_separation_layers(doc, res, on_progress=on_prog)
            self._set_status(f'Created {n} layers.')
            self.setVisible(False)
        except Exception as e:
            self._set_status(f'Layer error: {e}', error=True)
        finally:
            self._is_running = False
            if QApplication.overrideCursor():
                QApplication.restoreOverrideCursor()

    def _toggle_help(self):
        self._help_visible = not self._help_visible
        for c in self._controls.values():
            if hasattr(c, 'set_help_visible'):
                c.set_help_visible(self._help_visible)

    def _on_reread(self):
        if self._is_running:
            return
        self._proxy_pixels = None
        self._result = None
        self._has_result = False
        try:
            self._on_separate()
        except Exception as e:
            self._is_running = False
            if QApplication.overrideCursor():
                QApplication.restoreOverrideCursor()
            self._set_status(f'Reread failed: {e}', error=True)

    def _reset_all(self):
        from .constants import DEFAULTS
        for k, c in self._controls.items():
            val = self._archetype_defaults.get(k, DEFAULTS.get(k))
            c.set_value(val, programmatic=False)
        if self._has_result:
            self._schedule_rerun()

    def _to_pixmap(self, rgb: bytes, w: int, h: int) -> QPixmap:
        from PyQt5.QtGui import QImage
        img = QImage(rgb, w, h, w * 3, QImage.Format_RGB888).copy()
        return QPixmap.fromImage(img)

    def _set_status(self, msg, error=False):
        if not self._ui_built:
            return
        c = '#cc4444' if error else '#bbb'
        self._status_bar.setStyleSheet(f'color: {c}; font-size: 11px;')
        self._status_bar.setText(msg)
        self._side_status.setStyleSheet(f'color: {c}; font-size: 11px;')
        self._side_status.setText(msg)
        self._preview.set_overlay_text(msg if self._is_running else '')
        if self._is_running and msg:
            self._overlay.set_text(msg)
        else:
            self._overlay.set_text("")

    def _check_numpy(self):
        if self._numpy_warned:
            return
        self._numpy_warned = True
        try:
            import numpy
            log("numpy found.")
        except:
            log("WARNING: numpy NOT found.")
            if self._ui_built:
                self._set_status("Numpy not found - slow mode", error=True)

    def showEvent(self, e):
        super().showEvent(e)
        if not self._ui_built:
            self._build_ui()
        if self._is_running:
            self._is_running = False
            self._rerun_timer.stop()
            if QApplication.overrideCursor():
                QApplication.restoreOverrideCursor()
            self._set_status('Ready')
        if not self._startup and not self._has_result and not self._is_running:
            doc = Krita.instance().activeDocument()
            if doc and doc.colorModel() == 'LABA':
                QTimer.singleShot(200, self._on_separate)

    def canvasChanged(self, canvas):
        if not self._ui_built:
            return
        try:
            if not self._archetype_combo.isVisible():
                pass
        except RuntimeError:
            return
        self._proxy_pixels = None
        self._result = None
        self._has_result = False
        self._archetype_scores = []
        self._archetype_list = []
        self._others_expanded = False
        self._last_archetype_id = ''
        self._preview.clear_images()
        self._palette_mgr.clear_swatches()
        self._archetype_combo.blockSignals(True)
        self._archetype_combo.clear()
        self._archetype_combo.blockSignals(False)
        self._btn_separate.setEnabled(False)
        self._set_status('Ready')
