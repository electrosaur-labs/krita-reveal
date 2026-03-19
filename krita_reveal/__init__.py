"""
krita_reveal — Reveal colour separation plugin for Krita.

Registers a Tools > Scripts > Reveal Separation… action that opens
a free-floating dialog. No dock widget.
"""

import sys
import os

_vendor = os.path.join(os.path.dirname(__file__), 'vendor')
if _vendor not in sys.path:
    sys.path.insert(0, _vendor)

from .extension import RevealExtension

Application.addExtension(RevealExtension(Application))


def setup():
    pass
