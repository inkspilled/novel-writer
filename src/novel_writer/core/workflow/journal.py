"""工作流运行日志（JSONL）— 借鉴 DSH 的 session log / trajectory。

每次 run 在项目 `runs/` 下落一个 append-only 的 JSONL：
run_start / step_start / step_end / step_error / step_retry / tool_call /
tool_result / story_track / run_end。只记结构化事实（步骤、耗时、token、工具名），不落
正文，便于长跑排障与成本审计。
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path


class RunJournal:
    def __init__(self, project_dir: Path, name: str = "run"):
        run_dir = Path(project_dir) / "runs"
        run_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = run_dir / f"{stamp}-{os.getpid()}-{name}.jsonl".replace(" ", "_")

    def record(self, event: str, **fields) -> None:
        payload = {"ts": round(time.time(), 3), "event": event, **fields}
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except OSError:
            # 日志失败绝不能影响工作流
            pass
