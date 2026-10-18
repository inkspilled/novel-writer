"""分层上下文组装 — 控制注入 LLM 的信息量。

Tier 0 (GLOBAL):   项目元信息
Tier 1 (WORLD):    + 世界设定/角色状态/角色约束
Tier 2 (NARRATIVE):+ 剧情摘要/伏笔/反模式/追读力
Tier 3 (WORKING):  + 章节概要/全文/推演/RAG
"""
from __future__ import annotations

import re
from enum import Enum
from pathlib import Path

from .. import project_io
from ..anti_patterns import AntiPatternTracker
from ..character_sim import load_sim_cache
from ..memory import LOOP_OVERDUE_URGENCY, MemoryScratchpad, loop_urgency
from ..rag import BM25Index, Chunk, get_retriever
from ..reading_power import ReadingPowerTracker
from ..world_state import WorldState
from ..logger import get_logger
from .prompts import build_character_constraint

logger = get_logger(__name__)


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数：中文字符≈1.5，英文单词≈1.3。"""
    if not text:
        return 0
    cn_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    # 英文单词：连续的 ASCII 字符序列
    en_words = len(re.findall(r'[a-zA-Z]+', text))
    return int(cn_chars * 1.5 + en_words * 1.3)


# 上下文 token 预算（留空间给 system prompt + 输出）
# 48K：正文写作一章的规划+前文足够用；120K 会把每次调用的 prompt_tokens 顶满
MAX_CONTEXT_TOKENS = 48_000


def compress_context(parts: list[str], max_tokens: int = MAX_CONTEXT_TOKENS) -> list[str]:
    """动态压缩上下文：两级策略，先块内缩量，仍超限再整段丢弃。

    阶段一（缩量，保留信息骨架）：
    - RAG 块：只留前 2 条，每条正文截 120 字
    - 旧章全文：保留章号最大（最近）的一章完整，其余截 600 字
    - 规划块：相关片段留前 2 条截 100 字；摘要块截 300 字
    - 角色推演：截 600 字
    阶段二（整段丢弃，从先到后）：
    1. RAG（可重新检索） 2. 旧章全文（有摘要替代） 3. 规划文档（有摘要替代）
    4. 角色推演（非必需） 5. 章节概要（最后裁剪）
    """
    total = sum(estimate_tokens(p) for p in parts)
    if total <= max_tokens:
        return parts

    logger.info("上下文压缩触发: 当前≈%d tokens，目标≤%d tokens", total, max_tokens)
    orig_total = total

    # 按类型标记每个 part
    tagged = []
    for p in parts:
        if "=== RAG" in p or "[相关度:" in p:
            tagged.append(("rag", p))
        elif p.startswith("=== 第") and "章 ===\n" in p:
            tagged.append(("chapter_full", p))
        elif "=== 规划文档" in p or "（摘要）===" in p:
            tagged.append(("planning", p))
        elif "=== 角色推演 ===" in p:
            tagged.append(("sim", p))
        elif "=== 章节概要 ===" in p or "概要" in p[:20]:
            tagged.append(("summary", p))
        else:
            tagged.append(("other", p))

    # ── 阶段一：块内缩量 ──
    newest_ch = -1
    newest_idx = -1
    for i, (tag, p) in enumerate(tagged):
        if tag == "chapter_full":
            m = re.match(r"=== 第(\d+)章 ===", p)
            ch_num = int(m.group(1)) if m else -1
            if ch_num >= newest_ch:
                newest_ch, newest_idx = ch_num, i
    for i, (tag, p) in enumerate(tagged):
        if tag == "rag":
            tagged[i] = (tag, _shrink_rag_block(p, keep=2, text_cap=120))
        elif tag == "chapter_full" and i != newest_idx:
            tagged[i] = (tag, _truncate_block(p, cap=600))
        elif tag == "planning":
            tagged[i] = (tag, _shrink_planning_part(p))
        elif tag == "sim":
            tagged[i] = (tag, _truncate_block(p, cap=600))

    total = sum(estimate_tokens(p) for _, p in tagged)
    if total <= max_tokens:
        logger.info("阶段一缩量完成: ≤%d tokens", total)
        return [p for _, p in tagged]

    # ── 阶段二：整段丢弃 ──
    # other（无标记块）是项目元信息等关键上下文，刻意不参与裁剪——宁可略超预算也不丢
    priority_order = ["rag", "chapter_full", "planning", "sim", "summary"]

    for pri in priority_order:
        # tagged 已是真实剩余集合，直接用其 token 和；不能再减历史 removed
        total_now = sum(estimate_tokens(p) for _, p in tagged)
        if total_now <= max_tokens:
            break
        new_tagged = []
        for tag, p in tagged:
            if tag == pri and total_now > max_tokens:
                total_now -= estimate_tokens(p)
                logger.debug("裁剪 [%s]: %s... (剩余≈%d tokens)",
                             tag, p[:50].replace("\n", " "), total_now)
            else:
                new_tagged.append((tag, p))
        tagged = new_tagged

    result = [p for _, p in tagged]
    final_tokens = sum(estimate_tokens(p) for p in result)
    logger.info("上下文压缩完成: %d → %d tokens (裁剪 %.1f%%)",
                orig_total, final_tokens, (1 - final_tokens / orig_total) * 100 if orig_total else 0)
    return result


def _shrink_rag_block(part: str, keep: int, text_cap: int) -> str:
    """RAG 块缩量：保留前 keep 条，每条正文截 text_cap 字。"""
    lines = part.split("\n\n")
    header = lines[0]
    kept = []
    for entry in lines[1:1 + keep]:
        seg = entry.split("\n", 1)
        body = seg[1][:text_cap] if len(seg) > 1 else ""
        kept.append(f"{seg[0]}\n{body}")
    return "\n\n".join([header] + kept)


def _truncate_block(part: str, cap: int) -> str:
    """通用块缩量：保留标题行，正文截 cap 字。"""
    header, _, body = part.partition("\n")
    return f"{header}\n{body[:cap]}\n…（超限截断）"


def _shrink_planning_part(part: str) -> str:
    """规划块缩量：相关片段块留前 2 条，摘要块截 300 字。"""
    if part.startswith("=== 规划文档相关片段 ==="):
        return _shrink_rag_block(part, keep=2, text_cap=100)
    return _truncate_block(part, cap=300)


# ── 上下文分层 ──

class ContextTier(Enum):
    """上下文层级 — 控制注入 LLM 的信息量。"""
    GLOBAL = 0      # 仅全局前缀（Agent system_prompt + 项目元信息）
    WORLD = 1       # + 世界观层（世界设定/角色状态/角色约束）
    NARRATIVE = 2   # + 叙事记忆层（剧情摘要/伏笔/反模式/追读力）
    WORKING = 3     # + 工作记忆层（章节概要/全文/推演/RAG）


# 步骤 → 上下文层级映射
STEP_CONTEXT_TIERS: dict[str, ContextTier] = {
    # 纯工具步骤（不需要 LLM 或只需极简上下文）
    "toc": ContextTier.GLOBAL,
    "fix_titles": ContextTier.GLOBAL,
    "chapter_summary": ContextTier.GLOBAL,
    # 规划步骤（需要世界观，不需要叙事记忆）
    "ideation": ContextTier.GLOBAL,
    "outline": ContextTier.GLOBAL,
    "characters": ContextTier.WORLD,
    "world": ContextTier.WORLD,
    "timeline": ContextTier.WORLD,
    "main_plot": ContextTier.WORLD,
    "sub_plot": ContextTier.WORLD,
    "foreshadow": ContextTier.WORLD,
    "chapter_outline": ContextTier.WORLD,
    "update_chapter_outline": ContextTier.NARRATIVE,
    # 推演（需要角色设定，不需要叙事细节）
    "sim": ContextTier.WORLD,
    # 大世界状态更新（需要当前状态）
    "world_state_update": ContextTier.WORLD,
    # 灵感（需要剧情进展，不需要工作记忆）
    "inspiration": ContextTier.NARRATIVE,
    # 润色（需要风格指导，不需要完整世界观）
    "polish": ContextTier.NARRATIVE,
    # 审核（需要世界观+叙事，不需要工作记忆）
    "review": ContextTier.NARRATIVE,
    # 校对（只需要当前章节，由 ContextBuilder 特殊处理）
    "proofread": ContextTier.GLOBAL,
    # 质量检查（只需要当前章节）
    "quality_check": ContextTier.GLOBAL,
    # 摘要（需要叙事记忆）
    "summary": ContextTier.NARRATIVE,
    # 规划反哺（需要叙事记忆）
    "update_outline": ContextTier.NARRATIVE,
    "update_characters": ContextTier.NARRATIVE,
    "update_foreshadow": ContextTier.NARRATIVE,
    # 章节写作（完整上下文）
    "chapter": ContextTier.WORKING,
}


class ContextBuilder:
    """按步骤类型组装分层上下文。"""

    def __init__(
        self,
        project_dir: Path,
        project_info: dict,
        max_context_tokens: int = MAX_CONTEXT_TOKENS,
    ):
        self.project_dir = project_dir
        self.project_info = project_info
        self.max_context_tokens = max_context_tokens
        # 规划文档索引缓存（按文件 mtime 失效，避免每次调用重新分块）
        self._planning_cache_key: tuple | None = None
        self._planning_cache: BM25Index | None = None

    def build(self, input_files: list[str], n: int, step_id: str = "") -> str:
        """组装上下文 — 注意力优化布局。

        布局原则（对抗「U 形注意力曲线」）：
          头部（高注意力）= 硬约束 + 活性记忆卡 + 任务目标
          中部（参考区）  = 世界设定 + 规划文档 + 历史章节
          尾部（高注意力）= 未解伏笔 + 信息差 + 承接点 + 写作禁忌
        """
        tier = STEP_CONTEXT_TIERS.get(step_id, ContextTier.WORKING)

        # ── 校对步骤特殊处理：只注入当前章节 ──
        if step_id == "proofread":
            parts = [self._meta_line()]
            chapter_file = project_io.find_chapter_file(self.project_dir, n)
            if chapter_file:
                chapter_path = self.project_dir / project_io.CHAPTERS_DIR / chapter_file
                if chapter_path.exists():
                    content = project_io.read_md(chapter_path)
                    if content:
                        parts.append(content)
            return "\n\n".join(parts)

        if tier.value < ContextTier.WORLD.value:
            return self._assemble_input_files([self._meta_line()], input_files, n)

        # 准备数据源
        gs = WorldState(self.project_dir)
        gs.load()
        mem = MemoryScratchpad(self.project_dir)

        # ═══ 头部：硬约束 + 活性记忆（高注意力区） ═══
        head: list[str] = []
        meta = self._meta_line()
        if meta:
            head.append(meta)

        # 角色锚定硬约束 — 放最前面，确保模型最先看到
        char_file_content = ""
        planning_docs_for_rag: list[tuple[Path, str]] = []
        for f in input_files:
            if f == "prev_chapters":
                continue
            for p, content in self._resolve_input_files(f, n):
                if p.name.endswith("人物设定.md"):
                    char_file_content = content
                else:
                    planning_docs_for_rag.append((p, content))

        if char_file_content:
            constraint = build_character_constraint(char_file_content)
            if constraint:
                head.append(constraint)

        # 活性记忆卡：按本章相关实体（从大纲提取）精准拉取状态
        active_cards = self._build_active_memory_cards(gs, mem, n)
        if active_cards:
            head.append(active_cards)

        # ═══ 中部：参考材料（可被压缩裁剪） ═══
        middle: list[str] = []
        self._add_world_brief(middle, gs)
        char_states = build_merged_character_states(gs, mem)
        if char_states:
            middle.append(char_states)
        rel_rules = self._build_rel_rules_block(mem)
        if rel_rules:
            middle.append(rel_rules)

        # 本章大纲段直注入：最关键的「本章写什么」确定性注入，不靠检索碰运气
        if step_id == "chapter":
            outline_path = self.project_dir / "planning" / "大纲.md"
            outline_text = project_io.read_md(outline_path) if outline_path.exists() else ""
            section = extract_chapter_section(outline_text, n) if outline_text else ""
            if section:
                middle.append(f"=== 本章大纲 ===\n{section}")
            # 全书章节细纲：只注入本章骨架，避免整份灌入
            beats_path = self.project_dir / "planning" / "章节细纲.md"
            beats_text = project_io.read_md(beats_path) if beats_path.exists() else ""
            beats = extract_chapter_section(beats_text, n) if beats_text else ""
            if beats:
                middle.append(f"=== 本章细纲 ===\n{beats}")

        # 规划文档：RAG 片段
        if planning_docs_for_rag:
            rag_parts = self._rag_planning_fragments(planning_docs_for_rag, step_id, n)
            if rag_parts is None:
                for p, content in planning_docs_for_rag:
                    middle.append(f"=== {p.name}（摘要）===\n{content[:500]}")
            elif rag_parts:
                middle.append(rag_parts)

        if tier.value < ContextTier.NARRATIVE.value:
            # 低层级：头+中，尾部只保留最少
            tail = self._build_attention_tail(mem, gs, n, minimal=True)
            return "\n\n".join(head + middle + tail)

        # 滚动剧情摘要（summary 步骤每 5 章产出，写作时回流——修复长期只写盘未注入的死信）
        rolling_summary = project_io.latest_summary(self.project_dir)
        if rolling_summary:
            middle.append(f"=== 剧情摘要 ===\n{rolling_summary}")

        if tier.value >= ContextTier.WORKING.value:
            # 工作记忆也放中部（历史章节体量大）
            self._add_working_memory(middle, input_files, n, step_id)

        # ═══ 尾部：必须处理的活性信号（高注意力区） ═══
        tail = self._build_attention_tail(mem, gs, n, minimal=False)

        all_parts = head + middle + tail
        all_parts = compress_context(all_parts, self.max_context_tokens)
        result = "\n\n".join(all_parts)
        est_tokens = estimate_tokens(result)
        logger.debug("上下文组装完成: step=%s n=%d tier=%s tokens≈%d chars=%d",
                     step_id, n, tier.name, est_tokens, len(result))
        return result

    def _meta_line(self) -> str:
        if not self.project_info.get("title"):
            return ""
        meta = f"项目: {self.project_info['title']}"
        if self.project_info.get("genre"):
            meta += f" | 题材: {self.project_info['genre']}"
        if self.project_info.get("style"):
            meta += f" | 风格: {self.project_info['style']}"
        if self.project_info.get("theme"):
            meta += f" | 主题: {self.project_info['theme']}"
        return meta

    def _add_world_brief(self, parts: list[str], gs: WorldState):
        """世界设定（精简版：只输出世界名/时间/等级体系，不含角色详情）。"""
        w = gs.world
        if w.get("name"):
            world_brief = f"世界: {w['name']}"
            if w.get("current_date"):
                world_brief += f" | 时间: {w['current_date']}"
            if w.get("power_system", {}).get("levels"):
                world_brief += f" | 等级: {' → '.join(w['power_system']['levels'])}"
            parts.append(f"=== 世界设定 ===\n{world_brief}")

    def _build_rel_rules_block(self, mem: MemoryScratchpad) -> str:
        """章后沉淀的人物关系与世界规则（LLM 沉淀写入；空桶不出块）。

        放中部参考区：是「防写崩」的背景约束，不是本章必须处理的活性信号。
        """
        lines: list[str] = []
        for item in mem.get_active("relationships")[:4]:
            lines.append(f"【关系】{item.get('subject', '')}：{item.get('value', '')}"
                         f"（第{item.get('source_chapter', 0)}章）")
        for item in mem.get_active("world_rules")[:4]:
            lines.append(f"【规则】{item.get('subject', '')}：{item.get('value', '')}"
                         f"（第{item.get('source_chapter', 0)}章）")
        if not lines:
            return ""
        return "=== 关系与世界规则（近期沉淀） ===\n" + "\n".join(lines)

    def _resolve_input_files(self, f: str, n: int) -> list[tuple[Path, str]]:
        """解析单个 input_file 条目（支持通配符），返回 (路径, 内容) 列表。"""
        results: list[tuple[Path, str]] = []
        if "*" in f:
            target = self.project_dir / f
            parent = target.parent
            if parent.exists():
                for p in sorted(parent.glob(target.name)):
                    if p.is_file():
                        content = project_io.read_md(p)
                        if content:
                            results.append((p, content))
        else:
            # 只替换已知键，未知 {x} 原样保留（与 _format_prompt 同策略，防字面花括号炸 KeyError）
            values = {"n": n, **self.project_info}
            formatted = re.sub(
                r"\{(\w+)\}",
                lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0),
                f,
            )
            p = self.project_dir / formatted
            if not p.exists():
                # 章节文件可能以 {n}_标题.txt 命名，用 find_chapter_file 回退定位
                if f.startswith("chapters/") and "{n}" in f:
                    ch_name = project_io.find_chapter_file(self.project_dir, n)
                    if ch_name:
                        p = self.project_dir / "chapters" / ch_name
            if p.exists():
                content = project_io.read_md(p)
                if content:
                    results.append((p, content))
        return results

    def _planning_index(self, docs: list[tuple[Path, str]]) -> BM25Index | None:
        """规划文档索引 — 实例级缓存，按文件 mtime 失效。

        Returns:
            索引；文件状态读取异常返回 None（调用方回退到摘要模式）。
        """
        try:
            key = tuple((p.name, p.stat().st_mtime) for p, _ in docs)
        except OSError:
            return None
        if self._planning_cache_key == key and self._planning_cache is not None:
            return self._planning_cache
        index = BM25Index()
        for p, content in docs:
            sections = re.split(r"\n(?=#)|\n\n+", content)
            for i, section in enumerate(sections):
                section = section.strip()
                if len(section) < 20:
                    continue
                index.add_chunk(Chunk(
                    id=f"plan_{p.name}_s{i}",
                    chapter=0,
                    text=section,
                ))
        index.build()
        self._planning_cache_key = key
        self._planning_cache = index
        return index

    def _rag_planning_fragments(self, docs: list[tuple[Path, str]], step_id: str, n: int) -> str | None:
        """对规划文档做 BM25 检索，返回相关片段文本。

        查询优先用大纲中本章的描述段（与历史章节 RAG 同源、质量更高），
        大纲不在输入中时退化为「步骤 + 章号 + 书名」。

        Returns:
            片段文本；检索成功但无结果返回空串；索引构建异常返回 None（调用方回退到摘要）。
        """
        try:
            index = self._planning_index(docs)
            if index is None:
                return None
            query = ""
            for p, content in docs:
                if p.name == "大纲.md":
                    query = extract_chapter_query(content, n)
                    break
            if not query:
                parts = [step_id]
                if n > 1:
                    parts.append(f"第{n}章")
                if self.project_info.get("title"):
                    parts.append(self.project_info["title"])
                query = " ".join(parts)
            results = index.search(query, top_k=5)
            if not results:
                return ""
            rag_parts = ["=== 规划文档相关片段 ==="]
            for chunk, score in results:
                rag_parts.append(f"[相关度:{score:.1f}]\n{chunk.text[:300]}")
            return "\n\n".join(rag_parts)
        except Exception as e:
            logger.warning("规划文档 RAG 检索失败，回退到摘要模式: %s", e)
            return None

    def _add_working_memory(self, parts: list[str], input_files: list[str], n: int, step_id: str):
        # 上章回顾机制 - 提升章节连贯性
        if step_id == "chapter" and n > 1:
            prev_chapter_file = project_io.find_chapter_file(self.project_dir, n - 1)
            if prev_chapter_file:
                m = project_io._CHAPTER_RE.match(prev_chapter_file)
                if m:
                    prev_title = m.group(2)
                    prev_summary_path = project_io.chapter_summary_path(self.project_dir, n - 1, prev_title)
                    if prev_summary_path.exists():
                        prev_summary = project_io.read_md(prev_summary_path)
                        if prev_summary:
                            parts.append(f"=== 上一章要点回顾 ===\n【第{n-1}章《{prev_title}》】\n{prev_summary}\n\n注意：本章要承接上章剧情，保持连贯性。")

        # 本章灵感（inspiration 步骤产出回流——修复长期只写盘未注入的死信）
        if step_id == "chapter":
            inspiration = self._load_inspiration(n)
            if inspiration:
                parts.append(f"=== 灵感 ===\n{inspiration}")

        tick_text = self._load_offscreen_tick(n)
        if tick_text:
            parts.append(f"=== 离场角色近况（作者参考） ===\n{tick_text}")

        # 角色 POV 提案（作者参考；角色侧只写 sim_cache/pov/{id}/sim_{n}.json）
        pov_text = self._load_pov_proposals(n)
        if pov_text:
            parts.append(f"=== 角色当前意图（作者参考） ===\n{pov_text}")

        # 角色推演结果
        sim_text = load_sim_cache(self.project_dir, n)
        if sim_text:
            parts.append(sim_text)

        # 章节概要（最近5章）
        chapter_summaries = project_io.load_chapter_summaries(self.project_dir, up_to_chapter=n, window=5)
        if chapter_summaries:
            parts.append(chapter_summaries)

        # 最近2章全文 + RAG
        for f in input_files:
            if f == "prev_chapters":
                self._add_recent_chapters(parts, n)
                self._add_chapter_rag(parts, n)

    # ── 注意力优化布局：头部活性记忆卡 + 尾部高注意力信号 ──

    def _extract_chapter_entities(self, n: int) -> list[str]:
        """从大纲中提取第 n 章涉及的实体名（角色/地点）。

        用确定性规则而非语义检索：扫描大纲该章描述段中出现的已知角色名和地点名。
        """
        entities: list[str] = []
        outline_path = self.project_dir / "planning" / "大纲.md"
        if not outline_path.exists():
            return entities
        outline = project_io.read_md(outline_path)
        if not outline:
            return entities

        # 提取第 n 章的描述段
        query = extract_chapter_query(outline, n)
        if not query:
            return entities

        # 从 world_state 拉取已知实体名，逐一在本章描述中查找
        gs = WorldState(self.project_dir)
        gs.load()
        for name in gs.characters:
            if name and name in query:
                entities.append(name)
        for loc_name in (gs.world.get("locations") or {}):
            if loc_name and loc_name in query:
                entities.append(loc_name)

        # 若未命中任何已知实体，回退到 memory 中最近出现过的角色
        if not entities:
            mem = MemoryScratchpad(self.project_dir)
            for item in mem.get_active("character_state")[:5]:
                subj = item.get("subject", "")
                if subj and len(subj) >= 2:
                    entities.append(subj)
        return entities[:8]  # 最多 8 个实体

    def _build_active_memory_cards(self, gs: WorldState, mem: MemoryScratchpad, n: int) -> str:
        """构建活性记忆卡：只注入本章相关实体的当前状态（精准注入，非全量 dump）。

        设计原则：把「本章要写的角色/地点的活信息」放在上下文头部高注意力区，
        而不是淹没在中部的全量角色状态表里。
        """
        entities = self._extract_chapter_entities(n)
        if not entities:
            return ""

        lines = [f"=== 活性记忆卡（第{n}章相关实体） ==="]
        for name in entities:
            card = [f"【{name}】"]

            # world_state 结构化状态（角色）
            char = gs.get_character(name)
            if char:
                cult = char.get("cultivation", {})
                if cult:
                    card.append(f"  境界: {cult.get('level', '?')}{cult.get('sub_level', '')}")
                if char.get("location"):
                    card.append(f"  位置: {char['location']}")
                if char.get("hp") is not None:
                    card.append(f"  生命: {char['hp']}  灵力: {char.get('sp', 0)}")
                inv = char.get("inventory", [])
                if inv:
                    card.append(f"  持有: {', '.join(i.get('name', '?') for i in inv[:5])}")
                skills = char.get("skills", [])
                if skills:
                    card.append(f"  技能: {', '.join(skills[:5])}")
            else:
                # 地点或其他实体
                loc_info = (gs.world.get("locations") or {}).get(name)
                if loc_info is not None:
                    if isinstance(loc_info, dict):
                        desc = loc_info.get("desc") or loc_info.get("description") or ""
                        card.append(f"  描述: {desc[:80]}" if desc else "  地点")
                    elif isinstance(loc_info, str):
                        card.append(f"  描述: {loc_info[:80]}")
                    else:
                        card.append("  地点")

            # memory 中该实体的活跃状态变化（近 3 章）
            for item in mem.get_active("character_state"):
                if item.get("subject") == name:
                    asp = item.get("aspect", "")
                    val = item.get("value", "")
                    ch = item.get("source_chapter", 0)
                    if ch >= n - 3:
                        card.append(f"  [{asp}] {val}（第{ch}章）")

            # 该实体的资源得失
            for item in mem.get_active("resources"):
                if item.get("subject") == name:
                    val = item.get("value", "")
                    ch = item.get("source_chapter", 0)
                    if ch >= n - 3:
                        card.append(f"  [资源] {val}（第{ch}章）")

            # 该实体的情绪
            for item in mem.get_active("emotional_arcs"):
                if item.get("subject") == name:
                    val = item.get("value", "")
                    ch = item.get("source_chapter", 0)
                    if ch >= n - 2:
                        card.append(f"  [情绪] {val}（第{ch}章）")

            if len(card) > 1:  # 不只标题行
                lines.append("\n".join(card))

        return "\n".join(lines) if len(lines) > 1 else ""

    def _build_attention_tail(self, mem: MemoryScratchpad, gs: WorldState, n: int, minimal: bool = False) -> list[str]:
        """构建注意力尾部：放在上下文末尾的高注意力区。

        尾部内容是模型生成前「最后看到」的信息，注意力权重最高，
        适合放必须处理的活性信号：未解伏笔、信息差、承接点、写作禁忌。
        """
        tail: list[str] = []
        total_chapters = self.project_info.get("target_chapters", 0)

        # ── 未解伏笔（必须回收的钩子） ──
        open_loops = mem.get_open_loops(limit=6, current_chapter=n, total_chapters=total_chapters)
        if open_loops:
            loop_lines = ["⚠ 【未解伏笔 · 需要在近期章节处理】"]
            for item in open_loops:
                subj = item.get("subject", "")
                val = item.get("value", "")
                ch = item.get("source_chapter", 0)
                age = max(0, n - ch)
                overdue = "，**已超期，本章优先回收**" if loop_urgency(
                    item, n, total_chapters) >= LOOP_OVERDUE_URGENCY else ""
                loop_lines.append(f"- [{subj}] {val}（第{ch}章埋设，已经过{age}章{overdue}）")
            tail.append("\n".join(loop_lines))

        if minimal:
            return tail

        # ── 近期已回收伏笔（防止重复回收 / 把已解开的谜再挖开） ──
        closed = [
            i for i in mem.query("story_facts")
            if i.get("aspect") == "伏笔回收" and i.get("source_chapter", 0) >= n - 8
        ]
        if closed:
            closed.sort(key=lambda x: x.get("source_chapter", 0), reverse=True)
            c_lines = ["【近期已回收伏笔 · 不要重复回收】"]
            for item in closed[:4]:
                c_lines.append(f"- {item.get('subject', '')}（第{item.get('source_chapter', 0)}章已回收）")
            tail.append("\n".join(c_lines))

        # ── 读者承诺（待兑现的期待） ──
        promises = mem.get_active("reader_promises")[:4]
        if promises:
            p_lines = ["⚠ 【读者承诺 · 待兑现】"]
            for item in promises:
                subj = item.get("subject", "")
                val = item.get("value", "")
                p_lines.append(f"- [{subj}] {val}")
            tail.append("\n".join(p_lines))

        # ── 信息差（谁知道什么/谁不知道什么） ──
        info_items = mem.get_active("info_boundary")[:6]
        if info_items:
            i_lines = ["⚠ 【信息边界 · 避免穿帮】"]
            for item in info_items:
                subj = item.get("subject", "")
                asp = item.get("aspect", "")
                val = item.get("value", "")
                if "未知" in asp or item.get("payload", {}).get("hidden"):
                    i_lines.append(f"- {subj} **不知道**：{val}")
                else:
                    i_lines.append(f"- {subj} 已知：{val}")
            tail.append("\n".join(i_lines))

        # ── 上章承接点 ──
        if n > 1:
            prev_summary_path = None
            prev_file = project_io.find_chapter_file(self.project_dir, n - 1)
            if prev_file:
                m = project_io._CHAPTER_RE.match(prev_file)
                if m:
                    prev_summary_path = project_io.chapter_summary_path(self.project_dir, n - 1, m.group(2))
            if prev_summary_path and prev_summary_path.exists():
                content = project_io.read_md(prev_summary_path)
                # 只取「承接点」行
                for line in content.split("\n"):
                    if "承接" in line:
                        tail.append(f"🔗 【上章承接点】{line.strip()}")
                        break

        # ── 反模式禁忌 + 追读力指导（写作约束，放尾部强化） ──
        tracker = AntiPatternTracker(self.project_dir)
        constraint_text = tracker.get_constraint_text()
        if constraint_text:
            tail.append(constraint_text)

        rp_tracker = ReadingPowerTracker(self.project_dir)
        rp_guidance = rp_tracker.build_guidance(n)
        if rp_guidance:
            tail.append(rp_guidance)

        return tail

    def _load_inspiration(self, n: int) -> str:
        """加载本章灵感文件（inspiration 步骤的 {n}_*.md 产出）。"""
        insp_dir = self.project_dir / "inspiration"
        if not insp_dir.exists():
            return ""
        for f in sorted(insp_dir.glob(f"{n}_*.md")):
            text = project_io.read_md(f)
            if text:
                return text
        return ""

    def _add_recent_chapters(self, parts: list[str], n: int):
        chapters_dir = self.project_dir / project_io.CHAPTERS_DIR
        if not chapters_dir.exists():
            return
        start = max(1, n - 2)
        for i in range(start, n):
            for ch_file in sorted(chapters_dir.glob(f"{i}_*.txt")):
                if ch_file.name.endswith(".outline.md") or ch_file.name.endswith(".summary.md") \
                        or ch_file.name.endswith(".bak.txt"):
                    continue
                content = project_io.read_md(ch_file)
                if content:
                    parts.append(f"=== 第{i}章 ===\n{content}")

    def _load_offscreen_tick(self, n: int) -> str:
        """读取上一章的离场 tick，给写手做背景参考。"""
        import json
        path = self.project_dir / "sim_cache" / "offscreen" / f"tick_{max(1, n - 1)}.json"
        if not path.exists():
            return ""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            ticks = data.get("ticks") or []
            return "\n".join(
                f"【{t.get('character')}】{str(t.get('summary') or '')[:160]}"
                for t in ticks if isinstance(t, dict) and t.get("summary")
            )[:1200]
        except Exception:
            return ""

    def _load_pov_proposals(self, n: int) -> str:
        """读取 sim_cache/pov/*/sim_{n}.json，格式化给写手做参考。"""
        import json
        root = self.project_dir / "sim_cache" / "pov"
        if not root.exists():
            return ""
        lines: list[str] = []
        panel_path = root / f"panel_{n}.json"
        if panel_path.exists():
            try:
                panel = json.loads(panel_path.read_text(encoding="utf-8"))
                director = panel.get("director") or {}
                choice = str(director.get("choice") or "").strip()
                reason = str(director.get("reason") or "").strip()
                beats = director.get("selected_beats") or []
                if choice or beats:
                    line = "【导演裁决】"
                    if choice:
                        line += f"选择 {choice}"
                    if reason:
                        line += f"（{reason[:80]}）"
                    if beats:
                        line += "；" + "；".join(str(b)[:80] for b in beats[:3])
                    lines.append(line)
            except (OSError, json.JSONDecodeError):
                pass
        for path in sorted(root.glob(f"*/sim_{n}.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            name = str(data.get("_character_name") or path.parent.name)
            intent = str(data.get("intent") or "").strip()
            action = str(data.get("action") or "").strip()
            if not intent and not action:
                continue
            line = f"【{name}】"
            if intent:
                line += f"想要：{intent[:120]}"
            if action:
                line += f"；准备：{action[:120]}"
            lines.append(line)
        return "\n".join(lines[:4])

    def _add_chapter_rag(self, parts: list[str], n: int):
        """RAG 检索历史章节（基于大纲中本章描述作为查询）。"""
        try:
            rag = get_retriever(self.project_dir)
            rag.load_chapters(up_to_chapter=max(1, n - 2))
            outline_path = self.project_dir / "planning" / "大纲.md"
            if outline_path.exists():
                outline = project_io.read_md(outline_path)
                query = extract_chapter_query(outline, n)
                if query:
                    rag_text = rag.retrieve(query, top_k=5)
                    if rag_text:
                        parts.append(rag_text)
        except Exception as e:
            logger.debug("章节 RAG 检索跳过: %s", e)

    def _assemble_input_files(self, parts: list[str], input_files: list[str], n: int) -> str:
        """低层级步骤的输入文件组装（全量注入，不走 RAG）。"""
        for f in input_files:
            if f == "prev_chapters":
                continue
            for p, content in self._resolve_input_files(f, n):
                if "*" in f:
                    parts.append(f"=== {p.name} ===\n{content}")
                else:
                    parts.append(content)
        return "\n\n".join(parts)


def build_merged_character_states(gs: WorldState, mem: MemoryScratchpad) -> str:
    """合并 world_state 角色 + memory.character_state，去重输出。"""
    lines = []
    seen_chars = set()

    # 优先用 world_state 的角色数据（更结构化）
    for name, char in gs.characters.items():
        seen_chars.add(name)
        char_lines = [f"【{name}】"]
        cult = char.get("cultivation", {})
        if cult:
            char_lines.append(f"  境界: {cult.get('level', '?')}{cult.get('sub_level', '')}")
        if char.get("hp") is not None:
            char_lines.append(f"  生命: {char['hp']}  灵力: {char.get('sp', 0)}")
        if char.get("gold") is not None:
            char_lines.append(f"  灵石: {char['gold']}")
        if char.get("location"):
            char_lines.append(f"  位置: {char['location']}")
        inv = char.get("inventory", [])
        if inv:
            items = ", ".join(i.get("name", "?") for i in inv[:5])
            char_lines.append(f"  背包: {items}")
        skills = char.get("skills", [])
        if skills:
            char_lines.append(f"  技能: {', '.join(skills[:5])}")
        lines.append("\n".join(char_lines))

    # 补充 memory 中有但 world_state 没有的角色状态
    mem_chars = mem.get_active("character_state")
    for item in mem_chars:
        subj = item.get("subject", "")
        if subj and subj not in seen_chars:
            seen_chars.add(subj)
            val = item.get("value", "")
            asp = item.get("aspect", "")
            ch = item.get("source_chapter", 0)
            lines.append(f"【{subj}】{asp}: {val}（第{ch}章）")

    if not lines:
        return ""
    return "=== 角色状态 ===\n" + "\n".join(lines)


def extract_chapter_section(outline: str, n: int) -> str:
    """从大纲中提取第 n 章的完整描述段（标题行到下一个标题行前）。

    用于章节写作时的确定性直注入——「本章写什么」不应依赖检索命中。
    """
    lines = outline.split("\n")
    capture = False
    out: list[str] = []
    for line in lines:
        # 边界约束：n=1 不能命中「11.」「21.」等；n=10 不能命中「101.」
        if re.match(rf"^#.*第\s*{n}\s*章", line) or re.match(rf"^#.*(?<!\d){n}\.(?!\d)", line):
            capture = True
            out.append(line.rstrip())
            continue
        if capture:
            if re.match(r"^#", line):
                break
            out.append(line)
            if len(out) >= 40:  # 防异常大纲单段过长
                break
    return "\n".join(out).strip()[:2000]


def extract_chapter_query(outline: str, n: int) -> str:
    """从大纲中提取第 n 章的描述作为 RAG 查询。"""
    section = extract_chapter_section(outline, n)
    lines = [line for line in section.split("\n") if line.strip()]
    return " ".join(lines[:5]).strip()[:200]
