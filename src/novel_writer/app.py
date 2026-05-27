"""Novel Writer 应用入口。"""

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont, QIcon

# 支持直接运行和模块导入两种方式
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "novel_writer"

from novel_writer.ui.main_window import MainWindow

PKG_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PKG_DIR.parent.parent


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Novel Writer")
    app.setOrganizationName("NovelWriter")

    # 应用图标（dock + 窗口标题栏）
    icon_path = PROJECT_ROOT / "logo.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    font = QFont("PingFang SC", 14)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    app.setFont(font)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
