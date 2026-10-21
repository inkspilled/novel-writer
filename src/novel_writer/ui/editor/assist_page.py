"""写作台 — 把工作流生成但作者平时看不到的本章/全书资料集中到一个页面。

不写业务数据，只读：
- 本章：灵感 / 角色推演 / 细纲 / 概要 / 审校
- 全书：滚动摘要 / 审校中心 / 记忆 / 版本

设计原则：跟着当前章走；工作流产出后只刷新当前页，不打断写作。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QTextBrowser, QVBoxLayout, QWidget,
)

from ...core import project_io
from ...core.logger import get_logger
from ...core.workflow.context import extract_chapter_section

logger = get_logger(__name__)

# (分组名, [(key, 显示名), ...])
_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    ("本章", [
        ("inspiration", "灵感"),
        ("sim", "推演"),
        ("outline", "细纲"),
        ("summary", "概要"),
        ("review", "审校"),
    ]),
    ("全书", [
        ("rolling_summary", "滚动摘要"),
        ("review_center", "审校中心"),
        ("memory", "记忆"),
        ("versions", "版本"),
    ]),
]

_MEMORY_LABELS = {
    "character_state": "角色状态",
    "story_facts": "剧情事实",
    "world_rules": "世界规则",
    "timeline": "时间线",
    "open_loops": "未解伏笔",
    "reader_promises": "读者承诺",
    "relationships": "人物关系",
    "resources": "资源得失",
    "emotional_arcs": "情绪弧线",
    "info_boundary": "信息边界",
    "subplots": "支线进度",
}

_CHAPTER_KEYS = {"inspiration", "sim", "outline", "summary", "review"}


class AssistPage(QWidget):
    """写作台：只读资料页。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project_dir: Path | None = None
        self._chapter = 0
        self._title = ""
        self._selector_items: list[tuple[str, str]] = []
        self._build_ui()

    # ── UI ──

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        toolbar = QWidget()
        toolbar.setObjectName("editorToolbar")
        tl = QHBoxLayout(toolbar)
        tl.setContentsMargins(20, 10, 20, 10)
        tl.setSpacing(10)
        self.chapter_label = QLabel("写作台")
        self.chapter_label.setStyleSheet("font-size: 15px; font-weight: 700;")
        tl.addWidget(self.chapter_label, 1)

        self.selector = QComboBox()
        self.selector.setMinimumWidth(220)
        self.selector.setVisible(False)
        self.selector.currentIndexChanged.connect(self._render_selected)
        tl.addWidget(self.selector)

        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setFixedHeight(30)
        self.refresh_btn.clicked.connect(self.reload)
        tl.addWidget(self.refresh_btn)
        root.addWidget(toolbar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self.nav = QListWidget()
        self.nav.setObjectName("planningList")
        self.nav.setFixedWidth(132)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.currentItemChanged.connect(self._on_nav_changed)
        body.addWidget(self.nav)

        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(False)
        self.browser.setStyleSheet(
            "QTextBrowser { background: transparent; border: none; padding: 18px 24px;"
            " color: palette(text); }"
        )
        body.addWidget(self.browser, 1)
        root.addLayout(body, 1)

        for section, items in _SECTIONS:
            header = QListWidgetItem(section)
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            header.setData(Qt.ItemDataRole.UserRole, "")
            font = header.font()
            font.setBold(True)
            header.setFont(font)
            self.nav.addItem(header)
            for key, label in items:
                item = QListWidgetItem(f"  {label}")
                item.setData(Qt.ItemDataRole.UserRole, key)
                self.nav.addItem(item)

        self.nav.setCurrentRow(1)  # 默认「本章 → 灵感」

    # ── 外部 API ──

    def set_project_dir(self, project_dir: Path | None):
        self._project_dir = Path(project_dir) if project_dir else None
        self._chapter = 0
        self._title = ""
        self.chapter_label.setText("写作台" if not self._project_dir else "写作台 · 选择章节")
        self.reload()

    def set_chapter(self, number: int, title: str = ""):
        self._chapter = int(number or 0)
        self._title = title or ""
        if self._chapter:
            clean_title = re.sub(
                rf"^第\s*{self._chapter}\s*章\s*[:：]?\s*", "", self._title
            ).strip()
            suffix = f" {clean_title}" if clean_title else ""
            self.chapter_label.setText(f"写作台 · 第{self._chapter}章{suffix}")
        else:
            self.chapter_label.setText("写作台 · 选择章节")
        if self._current_key() in _CHAPTER_KEYS:
            self.reload()

    def select_key(self, key: str):
        """供测试/外部跳转：选中某个子页。"""
        for i in range(self.nav.count()):
            item = self.nav.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == key:
                self.nav.setCurrentItem(item)
                return

    def reload(self):
        self._render_current()

    # ── 导航/渲染 ──

    def _current_key(self) -> str:
        item = self.nav.currentItem()
        if item is None:
            return "inspiration"
        return str(item.data(Qt.ItemDataRole.UserRole) or "inspiration")

    def _on_nav_changed(self, current, _previous):
        if current is not None:
            self._render_current()

    def _render_current(self):
        key = self._current_key()
        self.selector.setVisible(False)
        if not self._project_dir:
            self._show("打开项目后显示本章资料。")
            return
        if key in _CHAPTER_KEYS and not self._chapter:
            self._show("先在左侧选择一章，或双击章节后再看。")
            return
        try:
            if key == "inspiration":
                self._show(self._read_inspiration())
            elif key == "sim":
                self._show(self._read_sim())
            elif key == "outline":
                self._show(self._read_outline())
            elif key == "summary":
                self._show(self._read_summary())
            elif key == "review":
                self._show(self._read_review())
            elif key == "rolling_summary":
                self._show(self._read_rolling_summary())
            elif key == "review_center":
                self._show_review_center()
            elif key == "memory":
                self._show(self._read_memory())
            elif key == "versions":
                self._show_versions()
        except Exception as e:
            logger.warning("写作台读取失败: %s", e, exc_info=True)
            self._show(f"读取失败：{e}")

    def _show(self, text: str, empty: str = ""):
        self.selector.setVisible(False)
        text = (text or "").strip()
        if not text:
            self.browser.setPlainText(empty or "暂无内容。")
            return
        self.browser.setMarkdown(text)

    def _show_selector(self, items: list[tuple[str, str]], empty: str):
        self._selector_items = items
        self.selector.blockSignals(True)
        self.selector.clear()
        for label, value in items:
            self.selector.addItem(label, value)
        self.selector.blockSignals(False)
        self.selector.setVisible(bool(items))
        if not items:
            self._show(empty)
            return
        self.selector.setCurrentIndex(0)
        self._render_selected()

    def _render_selected(self):
        idx = self.selector.currentIndex()
        if idx < 0 or idx >= len(self._selector_items):
            return
        _label, path = self._selector_items[idx]
        if path.startswith("__memory__"):
            self._show(self._read_memory())
            return
        p = Path(path)
        text = project_io.read_md(p) if p.exists() else ""
        self._show(text, f"文件为空：{p.name}")

    # ── 本章资料 ──

    def _read_inspiration(self) -> str:
        folder = self._project_dir / "inspiration"
        files = sorted(folder.glob(f"{self._chapter}_*.md")) if folder.exists() else []
        if not files:
            return "本章还没有灵感文件（工作流每 3 章生成一次，或该步被跳过）。"
        if len(files) == 1:
            return project_io.read_md(files[0])
        return "\n\n---\n\n".join(
            f"## {p.name}\n\n{project_io.read_md(p)}" for p in files
        )

    def _read_sim(self) -> str:
        n = self._chapter
        parts: list[str] = []
        sim_file = self._project_dir / "sim_cache" / f"sim_{n}.md"
        if sim_file.exists():
            parts.append(project_io.read_md(sim_file))

        panel_file = self._project_dir / "sim_cache" / "pov" / f"panel_{n}.json"
        if panel_file.exists():
            try:
                panel = json.loads(panel_file.read_text(encoding="utf-8"))
                director = panel.get("director") or {}
                lines = ["## 导演裁决"]
                if director.get("choice"):
                    lines.append(f"- 选择：{director['choice']}")
                if director.get("reason"):
                    lines.append(f"- 理由：{director['reason']}")
                beats = director.get("selected_beats") or []
                if beats:
                    lines.append("- 节拍：" + "；".join(str(b) for b in beats[:3]))
                parts.append("\n".join(lines))
            except (OSError, json.JSONDecodeError) as e:
                logger.debug("读取 panel_%s.json 失败: %s", n, e)

        pov_lines: list[str] = []
        pov_dir = self._project_dir / "sim_cache" / "pov"
        for path in sorted(pov_dir.glob(f"*/sim_{n}.json")) if pov_dir.exists() else []:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            name = str(data.get("_character_name") or path.parent.name)
            intent = str(data.get("intent") or "").strip()
            action = str(data.get("action") or "").strip()
            if intent or action:
                line = f"- **{name}**"
                if intent:
                    line += f"｜想要：{intent}"
                if action:
                    line += f"｜准备：{action}"
                pov_lines.append(line)
        if pov_lines:
            parts.append("## 角色意图\n" + "\n".join(pov_lines[:12]))
        return "\n\n".join(p for p in parts if p.strip()) or "本章还没有角色推演。"

    def _read_outline(self) -> str:
        n = self._chapter
        beats_path = self._project_dir / "planning" / "章节细纲.md"
        if beats_path.exists():
            section = extract_chapter_section(project_io.read_md(beats_path), n)
            if section.strip():
                return f"# 第{n}章 细纲\n\n{section}"
        chapter_file = project_io.find_chapter_file(self._project_dir, n)
        if chapter_file:
            m = project_io._CHAPTER_RE.match(chapter_file)
            if m:
                p = self._project_dir / "chapters" / project_io.chapter_outline_filename(n, m.group(2))
                if p.exists() and p.stat().st_size:
                    return project_io.read_md(p)
        return "本章还没有细纲（chapter_outline 步骤可能被跳过）。"

    def _read_summary(self) -> str:
        n = self._chapter
        folder = self._project_dir / "chapters"
        files = sorted(folder.glob(f"{n}_*.summary.md")) if folder.exists() else []
        if files:
            return project_io.read_md(files[-1])
        return "本章还没有概要（chapter_summary 步骤未跑）。"

    def _read_review(self) -> str:
        n = self._chapter
        candidates = [
            self._project_dir / "review" / f"质量检查_第{n}章.md",
            self._project_dir / "review" / f"校对报告_第{n}章.md",
        ]
        parts = []
        for p in candidates:
            if p.exists() and p.stat().st_size:
                parts.append(f"# {p.stem}\n\n{project_io.read_md(p)}")
        return "\n\n---\n\n".join(parts) or "本章还没有质量检查/校对报告。"

    # ── 全书资料 ──

    def _read_rolling_summary(self) -> str:
        return project_io.latest_summary(self._project_dir) or "全书滚动摘要还没生成（summary 每 5 章跑一次）。"

    def _show_review_center(self):
        folder = self._project_dir / "review"
        files = sorted(folder.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True) if folder.exists() else []
        items = [(p.name, str(p)) for p in files]
        self._show_selector(items, "review/ 下还没有审校报告。")

    def _read_memory(self) -> str:
        path = self._project_dir / "memory.json"
        if not path.exists():
            return "还没有 memory.json。"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            return f"memory.json 读取失败：{e}"
        lines = ["# 长期记忆", "", "> 只读视图。改记忆请直接编辑 memory.json（后续会加编辑/删除）。", ""]
        for bucket, items in data.items():
            if not isinstance(items, list):
                continue
            active = [i for i in items if isinstance(i, dict) and i.get("status") == "active"]
            label = _MEMORY_LABELS.get(bucket, bucket)
            lines.append(f"## {label}（{len(active)}）")
            if not active:
                lines.append("- 空")
                lines.append("")
                continue
            for item in active[:40]:
                subject = str(item.get("subject") or "").strip()
                aspect = str(item.get("aspect") or "").strip()
                value = str(item.get("value") or "").strip().replace("\n", " ")[:240]
                ch = item.get("source_chapter") or 0
                meta = f"[{aspect}] " if aspect else ""
                lines.append(f"- **{subject}** {meta}{value}（第{ch}章）")
            lines.append("")
        return "\n".join(lines)

    def _show_versions(self):
        folder = self._project_dir / "chapters" / "versions"
        files = sorted(folder.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True) if folder.exists() else []
        items = []
        for p in files:
            m = re.match(r"(\d+)_", p.name)
            label = f"第{m.group(1)}章 · {p.name}" if m else p.name
            items.append((label, str(p)))
        self._show_selector(items, "还没有章节版本（封印后编辑才会写入 versions/）。")
