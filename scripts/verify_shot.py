"""Numerically verify a screenshot isn't blank, and re-save it as a clean RGB PNG."""

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QImage  # noqa: E402

# Scratch screenshots live in the OS temp dir (never the repo) — see scripts/screenshot.py.
_DEFAULT_SRC = os.path.join(tempfile.gettempdir(), "vike-shots", "shot_full.png")
src = sys.argv[1] if len(sys.argv) > 1 else _DEFAULT_SRC
img = QImage(src)
if img.isNull():
    print(f"FAILED to read {src}")
    raise SystemExit(1)

w, h = img.width(), img.height()
colors = set()
nonbg = 0
step = 3
for y in range(0, h, step):
    for x in range(0, w, step):
        c = img.pixel(x, y)
        colors.add(c)
        if (c & 0xFFFFFF) not in (0x000000, 0x0E0E0E, 0xF0F0F0, 0xFFFFFF):
            nonbg += 1
print(f"{src}: {w}x{h}, distinct_colors={len(colors)}, non_background_samples={nonbg}")

out = src.replace(".png", "_rgb.png")
img.convertToFormat(QImage.Format_RGB888).save(out)
print(f"re-saved -> {out}")
