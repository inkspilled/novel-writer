"""per-call 用量流水（runs/usage.jsonl）+ 前缀指纹比对。

journal 的 step_end 只记步骤增量，正文流式与并发内置步骤的**单次调用**粒度
在这里补齐：每次 LLM 调用一行，含归一化 usage、调用级元数据、前后缀指纹与
劈点标记。布局改造（P1）前后的命中率对比全靠这份流水。

指纹口径（P0 简化版，segment 级首异点要等 ContextBuilder 返回结构化 segments）：
- fp_system：system 消息全文哈希。当前布局里 context 拼在 system 内、每章都变，
  所以它几乎每行都变——这正是 F1 的量化基线，改造后应变成「只在 update_* 时变」。
- fp_tools：工具 schema 哈希。是否计入缓存前缀各家未文档化（H6），先记录。
"""
from __future__ import annotations

import time
from pathlib import Path


class UsageLedger:
    """append-only 的 usage.jsonl；指纹状态按 (agent, model) 记忆上次值。"""

    def __init__(self, project_dir: Path, run: str = ""):
        run_dir = Path(project_dir) / "runs"
        run_dir.mkdir(parents=True, exist_ok=True)
        self.path = run_dir / "usage.jsonl"
        self.run = run
        self._last_fp: dict[tuple[str, str], tuple[str, str]] = {}

    def compare_prefix(self, usage: dict) -> tuple[int, int]:
        """与同 (agent, model) 上次调用比指纹，返回 (system是否变, tools是否变)。

        首次出现的 (agent, model) 记为基线（0, 0）——它确实全 miss，但不该把
        「冷启动」算成「前缀被改写」。
        """
        key = (str(usage.get("agent") or ""), str(usage.get("model") or ""))
        fp = (str(usage.get("fp_system") or ""), str(usage.get("fp_tools") or ""))
        prev = self._last_fp.get(key)
        self._last_fp[key] = fp
        if prev is None:
            return 0, 0
        return (1 if fp[0] != prev[0] else 0), (1 if fp[1] != prev[1] else 0)

    def record(self, fields: dict) -> None:
        payload = {"ts": round(time.time(), 3), "event": "llm_call", "run": self.run, **fields}
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(f"{_dumps(payload)}\n")
        except OSError:
            pass  # 流水失败绝不能影响工作流（与 RunJournal 同一约定）


def _dumps(payload: dict) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, default=str)
