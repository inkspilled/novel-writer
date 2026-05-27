"""生成应用图标和状态栏图标。"""
import sys
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtGui import QImage, QPainter, QIcon
from PySide6.QtCore import QSize, Qt

app = QApplication(sys.argv)
assets = Path(__file__).parent / "src" / "novel_writer" / "assets"

# ── 应用图标：渲染多尺寸 PNG → 生成 ICNS ──
icon_sizes = [16, 32, 64, 128, 256, 512, 1024]
renderer = QSvgRenderer(str(assets / "icon.svg"))

iconset_dir = assets / "AppIcon.iconset"
iconset_dir.mkdir(exist_ok=True)

for size in icon_sizes:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    renderer.render(p)
    p.end()
    # 1x
    img.save(str(iconset_dir / f"icon_{size}x{size}.png"))
    # 2x (只有 ≤512 才需要)
    if size <= 512:
        img2 = QImage(size * 2, size * 2, QImage.Format.Format_ARGB32)
        img2.fill(Qt.GlobalColor.transparent)
        p2 = QPainter(img2)
        renderer.render(p2)
        p2.end()
        img2.save(str(iconset_dir / f"icon_{size}x{size}@2x.png"))

print("PNG files generated in", iconset_dir)

# 用 iconutil 生成 ICNS（macOS only）
import subprocess
icns_path = assets / "AppIcon.icns"
result = subprocess.run(
    ["iconutil", "-c", "icns", str(iconset_dir), "-o", str(icns_path)],
    capture_output=True, text=True
)
if result.returncode == 0:
    print(f"ICNS generated: {icns_path}")
else:
    print(f"iconutil failed: {result.stderr}")

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

# 清理 iconset 临时文件
import shutil
shutil.rmtree(iconset_dir, ignore_errors=True)
print("Done!")
