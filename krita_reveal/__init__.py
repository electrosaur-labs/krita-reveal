"""
krita_reveal — Reveal colour separation plugin for Krita.
"""
import sys
import os

_vendor = os.path.join(os.path.dirname(__file__), 'vendor')
if _vendor not in sys.path:
    sys.path.insert(0, _vendor)

from .platform_paths import krita_data_dir
_packages = os.path.join(krita_data_dir(), 'python_packages')
if os.path.isdir(_packages) and _packages not in sys.path:
    sys.path.insert(0, _packages)

from krita import DockWidgetFactory, DockWidgetFactoryBase, Krita

try:
    from .dock import RevealDock
    from .extension import RevealExtension

    # Register DockWidget (UI panel)
    Krita.instance().addDockWidgetFactory(
        DockWidgetFactory(
            'reveal_separation_v2',
            DockWidgetFactoryBase.DockMinimized,
            RevealDock,
        )
    )

    # Register Extension (Tools > Scripts entry)
    Krita.instance().addExtension(RevealExtension(Krita.instance()))
    print("[Reveal] Plugin registered successfully.")
except Exception as e:
    import traceback
    print(f"[Reveal] FATAL ERROR during initialization: {e}")
    traceback.print_exc()

def setup():
    pass
