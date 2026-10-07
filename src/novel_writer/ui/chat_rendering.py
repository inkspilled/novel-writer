"""聊天消息渲染 — 颜色池与轻量 Markdown → HTML 转换。"""
from __future__ import annotations

import re

# 默认颜色池
COLOR_POOL = ["#ff6b8a", "#51cf66", "#4da6ff", "#ffd43b", "#cc5de8", "#ff922b",
              "#20c997", "#748ffc", "#f06595", "#5c7cfa", "#63e6be", "#e599f7"]

# Agent 面板共享状态（emoji/颜色映射、应用配置）
AGENT_PANEL_GLOBALS: dict = {"agent_emojis": {}, "agent_colors": {}, "config": {}}


def get_color(name: str, idx: int = 0) -> str:
    return AGENT_PANEL_GLOBALS["agent_colors"].get(name, COLOR_POOL[idx % len(COLOR_POOL)])


def _is_light_color(hex_color: str) -> bool:
    """判断颜色是否为浅色（用于 Markdown 渲染时动态选择代码块背景色）。"""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        r, g, b = int(h[:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        return False
    return (r * 299 + g * 587 + b * 114) / 1000 > 128


def _inline_md(text: str, fg: str = "#e8e8ed") -> str:
    """行内 Markdown：粗体、斜体、行内代码。"""
    code_bg = "rgba(0,0,0,0.06)" if _is_light_color(fg) else "rgba(255,255,255,0.08)"
    text = re.sub(r'`([^`]+)`',
                  r'<code style="background:' + code_bg + r';padding:1px 5px;'
                  r'border-radius:3px;font-family:Consolas,monospace;font-size:12px;">\1</code>', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)
    return text


def md_to_html(text: str, fg: str = "#e8e8ed") -> str:
    """轻量 Markdown → HTML，支持代码块、标题、列表、表格。"""
    is_light = _is_light_color(fg)
    code_bg = "rgba(0,0,0,0.06)" if is_light else "rgba(0,0,0,0.25)"
    code_fg = "#6e6e73" if is_light else "#a6adc8"
    border_color = "rgba(0,0,0,0.1)" if is_light else "rgba(255,255,255,0.1)"

    lines = text.split("\n")
    out: list[str] = []
    in_code_block = False
    in_list = False
    in_table = False
    table_rows: list[str] = []

    for line in lines:
        # 代码块
        if line.strip().startswith("```"):
            if in_code_block:
                out.append("</pre>")
                in_code_block = False
            else:
                if in_list:
                    out.append("</ul>")
                    in_list = False
                out.append(
                    f'<pre style="background:{code_bg};color:{code_fg};padding:10px 14px;'
                    'border-radius:8px;font-family:Consolas,monospace;font-size:12px;'
                    'overflow-x:auto;line-height:1.5;">')
                in_code_block = True
            continue
        if in_code_block:
            out.append(line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
            continue

        stripped = line.strip()

        # 表格
        if "|" in stripped and stripped.startswith("|"):
            if re.match(r'^\|[\s\-:|]+\|$', stripped):
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if not in_table:
                in_table = True
                table_rows = []
                header = "".join(
                    f'<th style="padding:6px 12px;border:1px solid {border_color};'
                    f'text-align:left;font-weight:600;">{c}</th>' for c in cells)
                table_rows.append(f"<tr>{header}</tr>")
            else:
                row = "".join(
                    f'<td style="padding:4px 12px;border:1px solid {border_color};">{c}</td>'
                    for c in cells)
                table_rows.append(f"<tr>{row}</tr>")
            continue
        elif in_table:
            out.append(
                f'<table style="border-collapse:collapse;margin:8px 0;width:100%;">'
                f'{"".join(table_rows)}</table>')
            in_table = False
            table_rows = []

        # 标题
        if stripped.startswith("### "):
            out.append(f'<b style="font-size:13px;">{stripped[4:]}</b><br>')
        elif stripped.startswith("## "):
            out.append(f'<b style="font-size:14px;">{stripped[3:]}</b><br>')
        elif stripped.startswith("# "):
            out.append(f'<b style="font-size:15px;">{stripped[2:]}</b><br>')
        # 无序列表
        elif stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                in_list = True
                out.append("<ul>")
            out.append(f"<li>{_inline_md(stripped[2:], fg)}</li>")
        # 有序列表
        elif re.match(r'^\d+\.\s', stripped):
            if not in_list:
                in_list = True
                out.append('<ul style="list-style-type:decimal;">')
            text = re.sub(r"^\d+\.\s", "", stripped)
            out.append(f'<li>{_inline_md(text, fg)}</li>')
        # 空行
        elif not stripped:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append("<br>")
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(_inline_md(stripped, fg))

    if in_list:
        out.append("</ul>")
    if in_table:
        out.append(
            f'<table style="border-collapse:collapse;margin:8px 0;width:100%;">'
            f'{"".join(table_rows)}</table>')
    if in_code_block:
        out.append("</pre>")

    return "<br>".join(out)


# 兼容别名
_md_to_html = md_to_html
