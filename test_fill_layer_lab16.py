# ── Option B test: Lab16 fill layer + transparency mask ───────────────────
# Run from Krita > Tools > Scripts > Script Console:
#   exec(open('/Users/shankar/development/electrosaur/krita-reveal/test_fill_layer_lab16.py').read())
#
# You need an open Lab16 document (Image > Properties confirms LABA / U16).
# Tests fill layer colour fidelity AND transparency mask application.

from krita import Krita, ManagedColor, InfoObject, Selection
import struct

app = Krita.instance()
doc = app.activeDocument()

# ── Sanity check ──────────────────────────────────────────────────────────
print("=== Option B: fill layer + transparency mask test ===")
if not doc:
    print("FAIL: no active document")
    raise SystemExit

w = doc.width()
h = doc.height()
print(f"  Document: {doc.colorModel()} / {doc.colorDepth()} / {doc.colorProfile()!r}")
print(f"  Size: {w} x {h}")

# ── Profile ───────────────────────────────────────────────────────────────
profiles = app.profiles("LABA", "U16")
lab_profile = (
    doc.colorProfile() if doc.colorModel() == "LABA"
    else "Lab identity built-in" if "Lab identity built-in" in profiles
    else (profiles[0] if profiles else "")
)
print(f"  Profile: {lab_profile!r}")

# ── Build fill layer (Lab L=50, a=60, b=40) ───────────────────────────────
L_val, a_val, b_val = 50.0, 60.0, 40.0
l_n = L_val / 100.0
a_n = (a_val * 256 + 32768) / 65535.0
b_n = (b_val * 256 + 32768) / 65535.0

c = ManagedColor("LABA", "U16", lab_profile)
c.setComponents([l_n, a_n, b_n, 1.0])
xml = c.toXML()

info = InfoObject()
info.setProperty("color", xml)

full_sel = Selection()
full_sel.select(0, 0, w, h, 255)

layer = doc.createFillLayer("TEST_B_fill+mask", "color", info, full_sel)
if not layer:
    print("FAIL: createFillLayer returned None")
    raise SystemExit
print(f"\ncreateFillLayer OK — type: {layer.type()!r}")

doc.rootNode().addChildNode(layer, None)
layer.setGenerator("color", info)
print("setGenerator OK")

# ── Build transparency mask — horizontal gradient left→right ─────────────
# x=0 → 255 (fully opaque), x=w-1 → 0 (fully transparent).
# Tests that intermediate mask values are honoured, not just 0 and 255.
print(f"\nBuilding mask: horizontal gradient (255 → 0 left to right) ...")
mask_bytes = bytearray(w * h)
for y in range(h):
    row_off = y * w
    for x in range(w):
        mask_bytes[row_off + x] = 255 - round(x / (w - 1) * 255)

tmask = doc.createTransparencyMask("mask")
layer.addChildNode(tmask, None)

mask_sel = Selection()
mask_sel.setPixelData(bytes(mask_bytes), 0, 0, w, h)
tmask.setSelection(mask_sel)
print("Transparency mask applied OK")

doc.refreshProjection()

print("\n=== VISUAL CHECK ===")
print("Layer 'TEST_B_fill+mask' added at top of stack.")
print(f"Expected: reddish-orange fading smoothly left (opaque) to right (transparent).")
print("If the fade is smooth -> intermediate mask values work (PASS).")
print("If only hard edges visible (no gradient) -> intermediate values broken (FAIL).")
print("If entirely blank/black -> fill layer failed (FAIL).")
print("\nDelete 'TEST_B_fill+mask' (and the earlier 'TEST_B_Lab16') when done.")
