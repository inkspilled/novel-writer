"""离线缓存模拟器：不调 API，重放当前布局的 prompt 组装，算理论命中率。

对应 docs/上下文缓存与1M预算.md 的 P0 性价比之王——「拿现有 20 章项目，
重放 1–20 章的 prompt 组装，算不同布局的理论命中率。选型不花钱。」

用法：
    python tools/cache_sim.py <项目目录> [--chapters 20] [--budget 48000]
                             [--ttl-chapters 0]

模型与近似（都是量级估算，不是精算）：
- 组装：完全复用线上 ContextBuilder + 步骤 prompt 模板，即「改造前」的基线布局
  （context 拼在 system 里、每章都变）。P1 的目标布局可在本文件上加第二个
  组装器做对照，这是它存在的意义。
- 伪 token：1 个 CJK 字 ≈ 1 token、1 个英文单词 ≈ 1 token——比线上 estimate_tokens
  （汉字×1.5）保守，接近真实 tokenizer 对中文的 0.6~0.8 token/字。
- 缓存：按 DeepSeek 口径模拟 64-token 块的前缀命中，对同 (agent) 的上一次请求
  求最长公共伪 token 前缀。--ttl-chapters 0 = 运行内不过期（乐观，DeepSeek 量级）；
  K = 隔超过 K 章就清缓存（悲观，Anthropic 5 分钟量级）。
- 历史仿真：按 NO_HISTORY_STEP_IDS 决定是否携带历史；assistant 回复取该章
  输出文件内容（真实项目跑过就有，没有则空串，命中率不受影响）。
- 覆盖范围：只模拟带 needs 的标准 LLM 步骤；digest/会审等内置步骤的 prompt
  在 builtin_steps 里，组装不可复用，不在基线内（量级小，不影响结论）。

输出：每调用一行 + 总计。对比实测时看 usage.jsonl 的 cached/est_tokens。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.novel_writer.core.agents import load_agents  # noqa: E402
from src.novel_writer.core.agents.base import AgentConfig, BaseAgent  # noqa: E402
from src.novel_writer.core.workflow.constants import NO_HISTORY_STEP_IDS  # noqa: E402
from src.novel_writer.core.workflow.context import ContextBuilder, estimate_tokens  # noqa: E402
from src.novel_writer.core.workflow.definition import _CYCLE_STEPS, WorkflowStep  # noqa: E402

BLOCK = 64  # DeepSeek 前缀缓存按 64 token 块命中

_WORD_RE = re.compile(r"[A-Za-z]+")


def pseudo_tokens(text: str) -> int:
    """伪 token 计数：CJK 字≈1、英文单词≈1、其余忽略。"""
    if not text:
        return 0
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    return cjk + len(_WORD_RE.findall(text))


def _format_prompt(template: str, project: dict, n: int) -> str:
    """与 runner._format_prompt 同口径：只替换已知键，未知 {x} 原样保留。"""
    values = {"n": n, **project}
    return re.sub(
        r"\{(\w+)\}",
        lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0),
        template,
    )


@dataclass
class CallRow:
    chapter: int
    step: str
    agent: str
    in_tokens: int
    hit_tokens: int

    @property
    def hit_pct(self) -> float:
        return self.hit_tokens / self.in_tokens * 100 if self.in_tokens else 0.0


class _CacheState:
    """同 agent 的上次请求前缀 + 所在章号（TTL 用）。"""

    def __init__(self) -> None:
        self.prefix = ""
        self.chapter = -1


def simulate(project_dir: Path, project_info: dict, steps: list[WorkflowStep],
             chapters: int, budget: int = 48_000, ttl_chapters: int = 0) -> list[CallRow]:
    agents = load_agents()

    def agent_for(needs: str) -> BaseAgent | None:
        # agents.json 的键就是名字，dict 值里没有 name/role 字段
        for name, cfg in agents.items():
            if needs in (cfg.get("skills") or []):
                return BaseAgent(AgentConfig(
                    name=str(name),
                    role=str(cfg.get("title") or name),
                    title=str(cfg.get("title") or name),
                    system_prompt=cfg.get("system_prompt") or "",
                ), None)
        return None

    builder = ContextBuilder(Path(project_dir), project_info, max_context_tokens=budget)
    cache: dict[str, _CacheState] = {}
    rows: list[CallRow] = []

    for n in range(1, chapters + 1):
        for step in steps:
            if step.every > 0 and n % step.every != 0:
                continue
            agent = agent_for(step.needs) if step.needs else None
            if agent is None:
                continue  # 内置步骤/无匹配智能体，不在基线内
            context = builder.build(list(step.input_files), n, step_id=step.id)
            prompt = _format_prompt(step.prompt, project_info, n)
            # 仿真 BaseAgent._build_messages 的消息数组（不含 guard/工具块）
            messages = [("system", agent.config.system_prompt + "\n\n" + context if context
                         else agent.config.system_prompt)]
            keep_history = step.id not in NO_HISTORY_STEP_IDS
            if keep_history:
                messages.extend(agent.history)
            messages.append(("user", prompt))

            serialized = "".join(role + "\x00" + content + "\x01" for role, content in messages)
            in_tokens = pseudo_tokens(serialized)

            state = cache.setdefault(agent.name, _CacheState())
            expired = ttl_chapters > 0 and state.chapter >= 0 and n - state.chapter > ttl_chapters
            if expired:
                state.prefix = ""
            common = _common_token_prefix(state.prefix, serialized)
            hit = (common // BLOCK) * BLOCK
            rows.append(CallRow(chapter=n, step=step.id, agent=agent.name,
                                in_tokens=in_tokens, hit_tokens=hit))
            state.prefix = serialized
            state.chapter = n

            # 历史回灌：assistant 回复取输出文件（真实项目有；否则空串占位）
            if keep_history:
                out_rel = step.output.replace("{n}", str(n))
                out_path = Path(project_dir) / out_rel
                answer = out_path.read_text(encoding="utf-8", errors="replace") if out_path.exists() else ""
                agent.history.append(("user", prompt))
                agent.history.append(("assistant", answer))
    return rows


def _common_token_prefix(prev: str, cur: str) -> int:
    """最长公共前缀（字符级），换算成伪 token 数。"""
    if not prev or not cur:
        return 0
    common = os.path.commonprefix([prev, cur])
    return pseudo_tokens(common)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线缓存命中率模拟（当前布局基线）")
    parser.add_argument("project_dir", help="项目目录")
    parser.add_argument("--chapters", type=int, default=20, help="模拟章数（默认 20）")
    parser.add_argument("--budget", type=int, default=48_000, help="上下文预算（默认与线上一致）")
    parser.add_argument("--ttl-chapters", type=int, default=0,
                        help="隔多少章后缓存视为过期；0=不过期（乐观）")
    args = parser.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    project_dir = Path(args.project_dir)
    if not project_dir.exists():
        print(f"项目目录不存在: {project_dir}")
        return 2

    steps = [WorkflowStep(
        id=s.get("id", ""), needs=s.get("needs", ""), prompt=s.get("prompt", ""),
        input_files=s.get("input", []), output=s.get("output", ""), every=s.get("every", 0),
    ) for s in _CYCLE_STEPS]
    from src.novel_writer.core import project_io

    info = {**(project_io.load_meta(project_dir) or {}), "target_chapters": args.chapters}
    rows = simulate(project_dir, info, steps, args.chapters,
                    budget=args.budget, ttl_chapters=args.ttl_chapters)
    if not rows:
        print("没有可模拟的 LLM 步骤（检查智能体技能是否匹配步骤 needs）")
        return 1

    print(f"{'章':>4} {'步骤':<14} {'智能体':<10} {'输入tok':>9} {'命中tok':>9} {'命中率':>7}")
    total_in = total_hit = 0
    for r in rows:
        total_in += r.in_tokens
        total_hit += r.hit_tokens
        print(f"{r.chapter:>4} {r.step:<14} {r.agent:<10} {r.in_tokens:>9} "
              f"{r.hit_tokens:>9} {r.hit_pct:>6.1f}%")
    overall = total_hit / total_in * 100 if total_in else 0.0
    print("-" * 60)
    print(f"合计 {len(rows)} 次调用 · 输入 {total_in} tok · 命中 {total_hit} tok · "
          f"整体命中率 {overall:.1f}%")
    print("说明：伪 token 口径（CJK 字≈1）；真实命中以供应商返回的 cached_tokens 为准。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
