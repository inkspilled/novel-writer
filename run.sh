#!/bin/bash
# Novel Writer 启动脚本
cd "$(dirname "$0")"

# 自动创建虚拟环境
if [ ! -d ".venv" ]; then
    echo "首次运行，创建虚拟环境..."
    python3 -m venv .venv
    .venv/bin/pip install -e . 2>&1 | tail -5
    echo "环境创建完成！"
fi

# 启动应用
.venv/bin/python3 -m novel_writer
