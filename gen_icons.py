"""生成应用图标和状态栏图标。"""
import sys
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtGui import QImage, QPainter
from PySide6.QtCore import Qt

app = QApplication(sys.argv)
project_root = Path(__file__).parent
pkg = project_root / "src" / "novel_writer"
assets = pkg / "assets"

# ── 应用 logo：1024x1024 PNG ──
renderer = QSvgRenderer(str(assets / "icon.svg"))
img = QImage(1024, 1024, QImage.Format.Format_ARGB32)
img.fill(Qt.GlobalColor.transparent)
p = QPainter(img)
renderer.render(p)
p.end()
img.save(str(project_root / "logo.png"))
print(f"logo.png generated: {project_root / 'logo.png'}")

# ── 状态栏图标：单色模板 PNG ──
status_renderer = QSvgRenderer(str(assets / "status_icon.svg"))
for px in [18, 36]:
    img = QImage(px, px, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    status_renderer.render(p)
    p.end()
    suffix = "@2x" if px == 36 else ""
    path = assets / f"status_icon{suffix}.png"
    img.save(str(path))
    print(f"Status icon: {path}")

print("Done!")
