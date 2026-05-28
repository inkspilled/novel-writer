"""章节追踪器 — 写后自动提取四维数据。

1. 资源账本：角色获得/失去了什么物品、金钱、能力
2. 情感弧线：角色当前情绪状态和变化
3. 信息边界：谁知道什么信息、谁不知道
4. 支线进度：各支线的推进/停滞/完结

从章节正文中用关键词 + 模式匹配提取，写入记忆暂存器。
"""
from __future__ import annotations

import re
from .memory import MemoryScratchpad, MemoryItem


def track_resources(mem: MemoryScratchpad, chapter: int, content: str):
    """追踪资源变化（获得/失去物品、金钱、能力）。"""
    # 获得模式
    gain_patterns = [
        (r"(\w{2,6})(获得|得到|拿到|捡到|收到|夺取|抢到)(了?)([^。，！？]{2,20})", "获得"),
        (r"(\w{2,6})（给了|递给|送给|赠予|交给)(了?)(\w{2,6})([^。，！？]{2,20})", "获得"),
    ]
    for pat, action in gain_patterns:
        for m in re.finditer(pat, content):
            groups = m.groups()
            if len(groups) >= 4:
                owner = groups[0]
                item = groups[3] if len(groups) > 3 else groups[-1]
                item = item.strip("了的")
                if 2 <= len(item) <= 20:
                    mem.upsert(MemoryItem(
                        category="resources",
                        subject=owner,
                        field=f"持有:{item}",
                        value=f"{action}「{item}」",
                        source_chapter=chapter,
                        payload={"action": action, "item": item},
                    ))

    # 失去模式
    loss_patterns = [
        (r"(\w{2,6})(失去|丢失|丢掉|损坏|破碎|用尽|消耗)(了?)([^。，！？]{2,20})"),
        (r"(\w{2,6})(被\w{1,4})(抢走|夺走|偷走|毁坏)(了?)([^。，！？]{2,20})"),
    ]
    for pat in loss_patterns:
        for m in re.finditer(pat, content):
            groups = m.groups()
            owner = groups[0]
            item = groups[-1].strip("了的")
            if 2 <= len(item) <= 20:
                mem.upsert(MemoryItem(
                    category="resources",
                    subject=owner,
                    field=f"失去:{item}",
                    value=f"失去「{item}」",
                    source_chapter=chapter,
                    payload={"action": "失去", "item": item},
                ))


def track_emotions(mem: MemoryScratchpad, chapter: int, content: str):
    """追踪角色情感弧线。"""
    emotion_keywords = {
        "愤怒": ["怒", "愤怒", "暴怒", "恼火", "气得", "咬牙", "握拳"],
        "悲伤": ["悲伤", "难过", "伤心", "泪", "哭泣", "哽咽", "心碎"],
        "恐惧": ["恐惧", "害怕", "畏惧", "颤抖", "惊恐", "毛骨悚然"],
        "喜悦": ["高兴", "开心", "喜悦", "笑了", "兴奋", "激动", "欢呼"],
        "惊讶": ["惊讶", "震惊", "目瞪口呆", "不敢相信", "惊愕", "没想到"],
        "平静": ["平静", "淡定", "冷静", "从容", "不动声色"],
        "坚定": ["坚定", "决心", "毅然", "毫不犹豫", "义无反顾"],
        "迷茫": ["迷茫", "困惑", "不解", "疑惑", "茫然"],
        "绝望": ["绝望", "崩溃", "万念俱灰", "心如死灰", "放弃"],
        "愧疚": ["愧疚", "自责", "内疚", "后悔", "懊悔"],
    }

    for emotion, keywords in emotion_keywords.items():
        for kw in keywords:
            if kw in content:
                # 尝试找到关联的角色
                # 在关键词附近找人名
                for m in re.finditer(r"(\w{2,6})[^。，！？]{0,10}" + re.escape(kw), content):
                    name = m.group(1)
                    if len(name) >= 2 and name not in ("这个", "那个", "什么", "怎么", "为什么"):
                        mem.upsert(MemoryItem(
                            category="emotional_arcs",
                            subject=name,
                            field=f"第{chapter}章情绪",
                            value=emotion,
                            source_chapter=chapter,
                            payload={"emotion": emotion, "keyword": kw},
                        ))
                        break  # 每个角色每种情绪只记一次


def track_info_boundary(mem: MemoryScratchpad, chapter: int, content: str):
    """追踪信息边界 — 谁知道了什么。"""
    # 知道/了解模式
    know_patterns = [
        (r"(\w{2,6})(得知|知道|了解|发现|明白|意识到|领悟到)(了?)([^。，！？]{5,40})"),
        (r"(\w{2,6})(告诉|告知|透露|坦白|解释)(了?)(\w{2,6})([^。，！？]{5,40})"),
    ]

    for pat in know_patterns:
        for m in re.finditer(pat, content):
            groups = m.groups()
            if len(groups) >= 3:
                knower = groups[0]
                info = groups[-1].strip("了的")
                if 5 <= len(info) <= 60:
                    mem.upsert(MemoryItem(
                        category="info_boundary",
                        subject=knower,
                        field="已知信息",
                        value=info[:60],
                        source_chapter=chapter,
                        payload={"info": info[:60]},
                    ))

    # 不知道/隐瞒模式
    hide_patterns = [
        (r"(\w{2,6})(不知道|不清楚|隐瞒|瞒着|没告诉|没说|欺骗)(\w{0,6})([^。，！？]{5,30})"),
    ]
    for pat in hide_patterns:
        for m in re.finditer(pat, content):
            groups = m.groups()
            who = groups[0]
            info = groups[-1].strip("了的")
            if 3 <= len(info) <= 40:
                mem.upsert(MemoryItem(
                    category="info_boundary",
                    subject=who,
                    field="未知信息",
                    value=info[:40],
                    source_chapter=chapter,
                    payload={"info": info[:40], "hidden": True},
                ))


def track_subplots(mem: MemoryScratchpad, chapter: int, content: str):
    """追踪支线进度。"""
    # 从大纲中提取支线名称（如果有）
    # 这里用关键词检测常见支线类型
    subplot_signals = {
        "感情线": ["喜欢", "爱", "心动", "表白", "在一起", "分手", "告白", "约会"],
        "复仇线": ["仇", "报复", "报仇", "清算", "偿还", "代价"],
        "成长线": ["突破", "修炼", "领悟", "变强", "晋升", "觉醒"],
        "探索线": ["探索", "发现", "秘密", "真相", "调查", "线索"],
        "权谋线": ["势力", "权力", "阴谋", "背叛", "联盟", "政变"],
        "冒险线": ["冒险", "任务", "挑战", "战斗", "对决", "试炼"],
    }

    for subplot, keywords in subplot_signals.items():
        matches = sum(1 for kw in keywords if kw in content)
        if matches >= 2:
            # 检测进度信号
            progress = "推进中"
            if any(kw in content for kw in ["完成", "结束", "解决", "告一段落"]):
                progress = "完结"
            elif any(kw in content for kw in ["停滞", "搁置", "暂时"]):
                progress = "停滞"

            # 提取关键句
            for kw in keywords:
                idx = content.find(kw)
                if idx >= 0:
                    start = max(0, idx - 10)
                    end = min(len(content), idx + 30)
                    snippet = content[start:end].replace("\n", " ")
                    mem.upsert(MemoryItem(
                        category="subplots",
                        subject=subplot,
                        field="进度",
                        value=f"{progress}: {snippet}",
                        source_chapter=chapter,
                        payload={"status": progress, "keywords": [kw]},
                    ))
                    break


def track_all(mem: MemoryScratchpad, chapter: int, content: str):
    """一次性提取所有四维数据。"""
    track_resources(mem, chapter, content)
    track_emotions(mem, chapter, content)
    track_info_boundary(mem, chapter, content)
    track_subplots(mem, chapter, content)
