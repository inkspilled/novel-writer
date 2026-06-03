"""Novel Writer - 日志配置模块

参照 Java Logback 配置，提供：
- info.log  — INFO 级别，按天滚动 + 单文件 100MB 上限，保留 30 天
- error.log — WARN 及以上，同样滚动策略
- 控制台输出 — 带彩色高亮

用法：
    from novel_writer.core.logger import get_logger
    logger = get_logger(__name__)
    logger.info("操作完成")
"""
import logging
import logging.handlers
import sys
import threading
from pathlib import Path


# ─── 日志目录 ───

def _get_log_dir() -> Path:
    """获取日志输出目录，兼容 PyInstaller 打包"""
    if getattr(sys, 'frozen', False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent.parent.parent.parent
    log_dir = base / "logs"
    log_dir.mkdir(exist_ok=True)
    return log_dir


LOG_DIR = _get_log_dir()

# ─── 日志格式 ───

# 与 Java Logback 一致：时间 [线程] 级别 logger - 消息
_LOG_FORMAT = "%(asctime)s [%(threadName)s] %(levelname)-5s %(name)s - %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 控制台彩色格式（ANSI 转义）
_COLOR_MAP = {
    "DEBUG": "\033[36m",     # 青色
    "INFO": "\033[32m",      # 绿色
    "WARNING": "\033[33m",   # 黄色
    "ERROR": "\033[31m",     # 红色
    "CRITICAL": "\033[35m",  # 紫色
}
_RESET = "\033[0m"


class _ColorFormatter(logging.Formatter):
    """控制台彩色日志格式化器"""

    def __init__(self, fmt=None, datefmt=None):
        super().__init__(fmt, datefmt)

    def format(self, record):
        color = _COLOR_MAP.get(record.levelname, "")
        record.levelname = f"{color}{record.levelname}{_RESET}"
        return super().format(record)


# ─── 滚动文件 Handler（按天 + 按大小） ───

class _DailySizeRotatingHandler(logging.Handler):
    """同时按日期和大小滚动的日志 Handler

    - 每天自动滚动（基于 TimedRotatingFileHandler）
    - 单文件超过 max_bytes 时也滚动
    - 保留 backup_count 天的日志
    """

    def __init__(self, filename, max_bytes=100 * 1024 * 1024, backup_count=30,
                 encoding="utf-8"):
        super().__init__()
        self._handler = logging.handlers.TimedRotatingFileHandler(
            filename=filename,
            when="midnight",
            interval=1,
            backupCount=backup_count,
            encoding=encoding,
            delay=True,
        )
        self._handler.suffix = "%Y-%m-%d"
        self._max_bytes = max_bytes

    def emit(self, record):
        try:
            # 检查是否需要按大小滚动
            if self._handler.stream and self._handler.stream.tell() >= self._max_bytes:
                self._handler.doRollover()
        except Exception:
            pass
        self._handler.emit(record)

    def setFormatter(self, fmt):
        super().setFormatter(fmt)
        self._handler.setFormatter(fmt)


# ─── 初始化日志系统 ───

_initialized = False
_init_lock = threading.Lock()


def _setup_logging():
    """初始化根日志配置（只执行一次）"""
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return

        root = logging.getLogger()
        root.setLevel(logging.DEBUG)

        # ── INFO 文件 ──
        info_handler = _DailySizeRotatingHandler(
            filename=str(LOG_DIR / "info.log"),
            max_bytes=100 * 1024 * 1024,
            backup_count=30,
        )
        info_handler.setLevel(logging.INFO)
        info_handler.addFilter(lambda record: record.levelno < logging.WARNING)
        info_handler.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
        root.addHandler(info_handler)

        # ── ERROR 文件 ──
        error_handler = _DailySizeRotatingHandler(
            filename=str(LOG_DIR / "error.log"),
            max_bytes=100 * 1024 * 1024,
            backup_count=30,
        )
        error_handler.setLevel(logging.WARNING)
        error_handler.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
        root.addHandler(error_handler)

        # ── 控制台 ──
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(_ColorFormatter(_LOG_FORMAT, _DATE_FORMAT))
        root.addHandler(console_handler)

        _initialized = True


def get_logger(name: str) -> logging.Logger:
    """获取 logger 实例（自动初始化日志系统）

    Args:
        name: logger 名称，通常传 __name__，如 'core.workflow'

    Returns:
        logging.Logger 实例
    """
    _setup_logging()
    return logging.getLogger(name)
