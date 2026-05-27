"""Novel Writer 应用入口。"""

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont

# 支持直接运行和模块导入两种方式
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "novel_writer"

from novel_writer.ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Novel Writer")
    app.setOrganizationName("NovelWriter")

    font = QFont("PingFang SC", 14)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    app.setFont(font)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
