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
import os
import sys
import threading
from pathlib import Path


# ─── 日志目录 ───

def _get_log_dir() -> Path:
    """获取日志输出目录，兼容 PyInstaller 打包。

    打包后写到用户数据目录，避免安装在 Program Files 时无权限在 exe 旁建 logs/。
    """
    if getattr(sys, 'frozen', False):
        if sys.platform == "win32":
            base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
            log_dir = Path(base) / "NovelWriter" / "logs"
        elif sys.platform == "darwin":
            log_dir = Path.home() / "Library" / "Application Support" / "NovelWriter" / "logs"
        else:
            base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
            log_dir = Path(base) / "novel-writer" / "logs"
    else:
        base = Path(__file__).resolve().parent.parent.parent.parent
        log_dir = base / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


LOG_DIR = _get_log_dir()

# ─── 日志配置常量（与 smt-intelligent/engine/core/logger.py 对齐） ───

# 单文件大小上限（100MB）
MAX_LOG_FILE_SIZE = 100 * 1024 * 1024

# 保留天数（滚动文件 info.log.YYYY-MM-DD 个数）
LOG_BACKUP_COUNT = 30

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
    """控制台彩色日志格式化器

    注意：绝不能永久改 record.levelname——同一 record 会流经多个 handler，
    改脏后文件日志会混入 ANSI 色码，还会因二次 format 产生重复行。
    """

    def format(self, record):
        orig = record.levelname
        color = _COLOR_MAP.get(orig, "")
        record.levelname = f"{color}{orig}{_RESET}"
        try:
            return super().format(record)
        finally:
            record.levelname = orig  # 还原，避免污染文件 handler


# ─── 滚动文件 Handler（按天 + 按大小） ───

class _DailySizeRotatingHandler(logging.Handler):
    """同时按日期和大小滚动的日志 Handler

    - 每天自动滚动（基于 TimedRotatingFileHandler）
    - 单文件超过 max_bytes 时也滚动
    - 保留 backup_count 天的日志
    """

    def __init__(self, filename, max_bytes: int = MAX_LOG_FILE_SIZE,
                 backup_count: int = LOG_BACKUP_COUNT, encoding: str = "utf-8"):
        super().__init__()
        self._handler = logging.handlers.TimedRotatingFileHandler(
            filename=filename,
            when="midnight",
            interval=1,
            backupCount=backup_count,
            encoding=encoding,
            delay=True,
        )
        # 滚动文件名：info.log.2026-10-14（与 smt-intelligent 一致）
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

        # 防重复挂 handler（多次 import/初始化会把每条日志写两遍）
        if getattr(root, "_nw_log_ready", False):
            _initialized = True
            return
        root._nw_log_ready = True

        # ── INFO 文件 ──
        info_handler = _DailySizeRotatingHandler(
            filename=str(LOG_DIR / "info.log"),
            max_bytes=MAX_LOG_FILE_SIZE,
            backup_count=LOG_BACKUP_COUNT,
        )
        info_handler.setLevel(logging.INFO)
        info_handler.addFilter(lambda record: record.levelno < logging.WARNING)
        info_handler.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
        root.addHandler(info_handler)

        # ── ERROR 文件 ──
        error_handler = _DailySizeRotatingHandler(
            filename=str(LOG_DIR / "error.log"),
            max_bytes=MAX_LOG_FILE_SIZE,
            backup_count=LOG_BACKUP_COUNT,
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
