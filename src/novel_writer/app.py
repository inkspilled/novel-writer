"""Novel Writer 应用入口。"""

import os
import sys
from pathlib import Path

# 禁用 shiboken 特性检查
os.environ['SHIBOKEN_DISABLE_FEATURE_IMPORT_CHECK'] = '1'

# PySide6 导入前：预导入 openai，避免 shiboken 拦截
try:
    import anyio
    import httpx
    import openai
except ImportError:
    pass

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont, QIcon

# PySide6 导入后：彻底禁用 shiboken 特性检查
try:
    import shibokensupport.feature as _feat
    _feat._mod_uses_pyside = lambda module: False
except (ImportError, AttributeError):
    pass

# 禁用 signature.loader 中的特性检查
try:
    import shibokensupport.signature.loader as _loader
    _loader.feature_imported = lambda *args, **kwargs: None
except (ImportError, AttributeError):
    pass

# monkey-patch inspect，防止任何残余的 getsource 调用卡死
import inspect as _inspect
_orig_gs = _inspect.getsource
def _safe_gs(obj):
    try:
        return _orig_gs(obj)
    except Exception:
        return ""
_inspect.getsource = _safe_gs

_orig_gsl = _inspect.getsourcelines
def _safe_gsl(obj):
    try:
        return _orig_gsl(obj)
    except Exception:
        return ([""], 0)
_inspect.getsourcelines = _safe_gsl

# 支持直接运行和模块导入两种方式
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "novel_writer"

from novel_writer.core.logger import get_logger
from novel_writer.ui.main_window import MainWindow

logger = get_logger(__name__)

PKG_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PKG_DIR.parent.parent


def main():
    logger.info("Novel Writer 启动中...")

    # Windows 任务栏图标必须设置 AppUserModelID，否则系统用默认图标
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Novel.Writer.App")
            logger.debug("已设置 Windows AppUserModelID")
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setApplicationName("Novel Writer")
    app.setOrganizationName("NovelWriter")

    # 应用图标（dock + 窗口标题栏 + Windows 任务栏）
    icon_path = PROJECT_ROOT / "logo.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
        logger.debug("应用图标已加载: %s", icon_path)
    else:
        logger.warning("应用图标未找到: %s", icon_path)

    _font_name = "Microsoft YaHei" if sys.platform == "win32" else "PingFang SC"
    font = QFont(_font_name, 14)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    app.setFont(font)

    window = MainWindow()
    window.show()
    logger.info("Novel Writer 已启动")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
