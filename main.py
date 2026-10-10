"""Novel Writer 入口 — 在项目根目录直接运行/点击启动。"""
import sys
from pathlib import Path

# 确保 src/ 在 Python 路径中
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from novel_writer.app import main

if __name__ == "__main__":
    main()
