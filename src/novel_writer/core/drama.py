"""短剧数据 — 依附于当前小说项目的 drama/ 目录，页面与工作流独立。

目录结构（小说项目根目录下）：
    drama/
    ├── series.json              # 剧集企划：平台/画幅/集数/单集时长/卖点
    ├── bible.md                 # 改编与视觉规则（人设/风格/台词/节奏）
    ├── episodes.json            # 分集元数据
    ├── episodes/ep001.md        # 分集剧本（Markdown，场次标题可解析）
    ├── storyboard/ep001.json    # 结构化镜头表
    ├── assets/index.json        # 资产注册表（角色/场景/道具）
    └── exports/                 # 导出的 CSV / JSON

设计原则：短剧不单独建项目；它依附当前小说，但 UI 是独立大页签，
工作流也不复用小说明章节循环。分镜是剧本的派生层，后续资产/生成再引用分镜 ID。
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .json_extract import extract_json_object
from .logger import get_logger
from . import project_io

logger = get_logger(__name__)

DRAMA_DIR = "drama"
EPISODES_DIR = "episodes"
STORYBOARD_DIR = "storyboard"
ASSETS_DIR = "assets"
EXPORTS_DIR = "exports"

SERIES_FILE = "series.json"
EPISODES_FILE = "episodes.json"
BIBLE_FILE = "bible.md"
ASSETS_FILE = "index.json"
SCHEMA_VERSION = 1

# 分镜字段（JSON 的 key -> 中文表头）。顺序即表格/CSV 的列顺序。
SHOT_COLUMNS: list[tuple[str, str]] = [
    ("shot_id", "镜号"),
    ("scene_id", "场次"),
    ("shot_size", "景别"),
    ("camera", "运镜"),
    ("duration_sec", "时长(秒)"),
    ("location", "地点"),
    ("time", "时间"),
    ("characters", "人物"),
    ("action", "画面/动作"),
    ("dialogue", "台词"),
    ("sfx", "音效"),
    ("transition", "转场"),
    ("status", "状态"),
    ("notes", "备注"),
]
SHOT_KEYS = [key for key, _label in SHOT_COLUMNS]

ASSET_COLUMNS: list[tuple[str, str]] = [
    ("kind", "类型"),
    ("id", "ID"),
    ("name", "名称"),
    ("source", "来源"),
    ("prompt", "提示词"),
    ("ref", "参考图"),
    ("status", "状态"),
    ("notes", "备注"),
]

ASSET_KIND_LABELS = {"character": "人物", "location": "场景", "prop": "道具"}

# 资产来源：正典（小说里有的）/ 分镜（短剧新增，如龙套、临时场景）/ 手工
ASSET_SOURCES = {"canon": "小说正典", "storyboard": "短剧原创", "manual": "手工"}
_ASSET_SOURCE_ALIASES = {"从小说导入": "canon", "从分镜自动提取": "storyboard"}

# 人物/场景/道具图统一用的负面词（本地模板，用户可改工作流模板）
ASSET_NEGATIVE = (
    "低清晰度，模糊，噪点，畸形手指，多余肢体，比例失调，脸部崩坏，"
    "文字，水印，logo，签名，多人重复，过曝，杂乱背景"
)

DEFAULT_SERIES: dict[str, Any] = {
    "title": "",
    "genre": "",
    "style": "",
    "platform": "抖音",
    "aspect": "9:16",
    "episode_count": 30,
    "episode_seconds": 90,
    "logline": "",
    "updated_at": "",
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _entity_summary(info: Any, cap: int = 200) -> str:
    """世界状态里 地点/物品 的条目摘要（dict 取前几个字符串字段）。"""
    if isinstance(info, str):
        return info.strip()[:cap]
    if not isinstance(info, dict):
        return ""
    bits: list[str] = []
    for key in ("desc", "description", "说明", "位置", "location", "用途", "note", "notes"):
        value = info.get(key)
        if isinstance(value, str) and value.strip():
            bits.append(value.strip())
    if not bits:
        bits = [str(v).strip() for v in info.values()
                if isinstance(v, str) and v.strip()]
    return "；".join(bits)[:cap]


def _normalize_asset_name(name: str) -> str:
    """资产名归一：去括号注解、空白与间隔号，用于正典匹配/去重。"""
    text = str(name or "").strip()
    text = re.sub(r"[（(][^）)]*[）)]", "", text)
    text = re.sub(r"[\s·・.。]+", "", text)
    return text


def _unique_asset_id(assets: list[dict[str, Any]], kind: str, name: str) -> str:
    base = re.sub(r"[^\w\u4e00-\u9fff]+", "_", name).strip("_") or "asset"
    prefix = {"character": "char", "location": "loc", "prop": "prop"}.get(kind, "asset")
    ids = {a.get("id") for a in assets}
    candidate = f"{prefix}_{base}"
    i = 2
    while candidate in ids:
        candidate = f"{prefix}_{base}_{i}"
        i += 1
    return candidate


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("读取短剧数据失败 %s: %s", path, e)
        return default
    return data if isinstance(data, type(default)) else default


def _write_json(path: Path, data: Any) -> None:
    project_io.write_json(path, data)


def episode_filename(number: int) -> str:
    return f"ep{max(1, int(number)):03d}"


def script_template(number: int, title: str = "") -> str:
    """一集剧本的最小模板：场次标题可被 stories_from_script 解析。"""
    heading = title or f"第{number}集"
    return (
        f"# {heading}\n\n"
        f"## 第1场 地点 · 日/夜\n\n"
        f"（画面/动作：先写观众能看见的东西，不写心理活动。）\n\n"
        f"角色：台词（短句、口语化，能一句说完不说两句。）\n\n"
        f"## 第2场 地点 · 日/夜\n\n"
        f"（本集反转/爽点落在这一场。）\n\n"
        f"角色：台词。\n\n"
        f"## 第3场 地点 · 日/夜\n\n"
        f"（集尾钩子：留一个必须点下一集的问题。）\n"
    )


def new_shot(episode: int, index: int) -> dict[str, Any]:
    """一条空镜头；后续由 UI/工作流填写。"""
    return {
        "shot_id": f"E{max(1, int(episode)):02d}-S{max(1, int(index)):03d}",
        "scene_id": "",
        "shot_size": "中景",
        "camera": "固定",
        "duration_sec": 3.0,
        "location": "",
        "time": "",
        "characters": "",
        "action": "",
        "dialogue": "",
        "sfx": "",
        "transition": "硬切",
        "status": "draft",
        "notes": "",
    }


def normalize_shot(shot: dict | None, episode: int = 1, index: int = 1) -> dict[str, Any]:
    base = new_shot(episode, index)
    if isinstance(shot, dict):
        for key, _label in SHOT_COLUMNS:
            if key in shot and shot[key] is not None:
                base[key] = shot[key]
    # 模型偶尔把人物/台词写成数组，统一成可编辑文本
    for key in ("characters", "action", "dialogue", "sfx", "notes", "location", "time"):
        value = base.get(key, "")
        if isinstance(value, (list, tuple)):
            value = "、".join(str(v) for v in value if str(v).strip())
        elif isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False)
        base[key] = str(value or "")
    try:
        base["duration_sec"] = float(base.get("duration_sec") or 0)
    except (TypeError, ValueError):
        base["duration_sec"] = 0.0
    if not math.isfinite(base["duration_sec"]) or base["duration_sec"] < 0:
        base["duration_sec"] = 0.0
    return base


def clean_script_output(text: str, episode: int, fallback_title: str = "") -> str:
    """清洗模型输出：去 think/代码块/前言，保证剧本以 # 第N集 和 ## 场次 开头。"""
    body = (text or "").strip()
    body = re.sub(r"<think>[\s\S]*?</think>", "", body, flags=re.IGNORECASE).strip()
    fence = re.search(r"```(?:markdown|md)?\s*([\s\S]*?)```", body, flags=re.IGNORECASE)
    if fence:
        body = fence.group(1).strip()
    # 去掉「好的，以下是剧本」这类前言，从第一个 # 第X集 开始
    match = re.search(r"^#\s*第\s*[0-9一二三四五六七八九十百]+\s*集.*$", body, flags=re.MULTILINE)
    if match:
        body = body[match.start():].strip()
    if not body.startswith("#"):
        title = fallback_title or f"第{episode}集"
        body = f"# 第{episode}集 {title}\n\n{body}"
    else:
        first, sep, rest = body.partition("\n")
        m = re.match(r"^#\s*第\s*[0-9一二三四五六七八九十百]+\s*集\s*(.*)$", first)
        if m:
            title = (m.group(1) or fallback_title or "").strip()
            body = f"# 第{episode}集 {title}".rstrip() + sep + rest
    if "##" not in body:
        body = body.rstrip() + f"\n\n## 第1场 {fallback_title or '本集'} · 日/夜\n\n（补场次）\n"
    return body.strip() + "\n"


def parse_storyboard_output(
    text: str, episode: int, default_duration: float = 3.0
) -> list[dict[str, Any]]:
    """解析模型输出的分镜 JSON；失败返回空列表。"""
    data = extract_json_object(text or "")
    if not isinstance(data, dict):
        return []
    raw_shots = data.get("shots")
    if not isinstance(raw_shots, list):
        return []
    shots: list[dict[str, Any]] = []
    for i, raw in enumerate(raw_shots[:100], start=1):
        if not isinstance(raw, dict):
            continue
        shot = normalize_shot(raw, episode, i)
        shot["shot_id"] = f"E{max(1, int(episode)):02d}-S{i:03d}"
        if not shot.get("duration_sec"):
            shot["duration_sec"] = float(default_duration)
        if not shot.get("scene_id"):
            shot["scene_id"] = f"SC{max(1, (i - 1) // 5 + 1):02d}"
        shots.append(shot)
    return shots


class DramaStore:
    """短剧项目数据访问层；不依赖 Qt，方便单测与后续工作流调用。"""

    def __init__(self, project_dir: Path | str):
        self.project_dir = Path(project_dir)
        self.root = self.project_dir / DRAMA_DIR
        self.episodes_dir = self.root / EPISODES_DIR
        self.storyboard_dir = self.root / STORYBOARD_DIR
        self.assets_dir = self.root / ASSETS_DIR
        self.exports_dir = self.root / EXPORTS_DIR

    # ── 初始化 ──

    def ensure(self, meta: dict | None = None) -> None:
        for sub in (self.episodes_dir, self.storyboard_dir, self.assets_dir, self.exports_dir):
            sub.mkdir(parents=True, exist_ok=True)
        meta = meta or {}
        if not (self.root / SERIES_FILE).exists():
            series = dict(DEFAULT_SERIES)
            series.update({
                "title": meta.get("title", ""),
                "genre": meta.get("genre", ""),
                "style": meta.get("style", ""),
                "episode_count": int(meta.get("target_chapters") or 30),
                "logline": meta.get("synopsis", ""),
                "updated_at": _now(),
            })
            _write_json(self.root / SERIES_FILE, series)
        if not (self.root / EPISODES_FILE).exists():
            _write_json(self.root / EPISODES_FILE, {
                "episodes": [{"number": 1, "title": "第1集", "goal": "", "hook": "", "status": "draft"}],
                "updated_at": _now(),
            })
            if not self.script_path(1).exists():
                self.save_script(1, script_template(1, "第1集"))
        if not (self.assets_dir / ASSETS_FILE).exists():
            _write_json(self.assets_dir / ASSETS_FILE, {"assets": [], "updated_at": _now()})
        if not (self.root / BIBLE_FILE).exists():
            (self.root / BIBLE_FILE).write_text("", encoding="utf-8")

    # ── 企划 ──

    def load_series(self) -> dict[str, Any]:
        series = dict(DEFAULT_SERIES)
        data = _read_json(self.root / SERIES_FILE, {})
        if isinstance(data, dict):
            series.update({k: v for k, v in data.items() if k in series})
        return series

    def save_series(self, data: dict[str, Any]) -> dict[str, Any]:
        series = self.load_series()
        for key in DEFAULT_SERIES:
            if key in data:
                series[key] = data[key]
        series["updated_at"] = _now()
        _write_json(self.root / SERIES_FILE, series)
        return series

    def bible_text(self) -> str:
        path = self.root / BIBLE_FILE
        try:
            return path.read_text(encoding="utf-8") if path.exists() else ""
        except OSError:
            return ""

    def save_bible(self, text: str) -> None:
        project_io.write_md(self.root / BIBLE_FILE, text or "")

    def build_bible_from_novel(self) -> str:
        """把小说规划文档汇总成改编圣经草稿（确定性，不调用 LLM）。"""
        planning = self.project_dir / "planning"
        docs = {
            "核心立意": "立意.md",
            "故事骨架": "大纲.md",
            "人物视觉锚点": "人物设定.md",
            "世界观": "世界观.md",
        }
        sections: list[str] = []
        for label, filename in docs.items():
            path = planning / filename
            try:
                body = path.read_text(encoding="utf-8").strip() if path.exists() else ""
            except OSError:
                body = ""
            sections.append(f"## {label}\n\n{body or '（小说规划里还没有这一节）'}")
        series = self.load_series()
        text = (
            f"# {series.get('title') or '短剧'} · 改编圣经（草稿）\n\n"
            "> 由小说规划文档自动汇总。AI 精修与短剧化规则下一阶段接入；\n"
            "> 这里先给出人物/世界观/故事骨架，避免改编时丢设定。\n\n"
            + "\n\n".join(sections)
            + "\n\n## 短剧化规则（待填）\n\n"
            f"- 平台/画幅：{series.get('platform')} / {series.get('aspect')}\n"
            f"- 集数与单集时长：{series.get('episode_count')} 集 × {series.get('episode_seconds')} 秒\n"
            "- 每集钩子：前 3 秒冲突、结尾 5 秒留问题。\n"
            "- 保留主线；支线只留能改变人物关系的。\n"
        )
        self.save_bible(text)
        return text

    # ── 剧本 ──

    def load_episodes(self) -> list[dict[str, Any]]:
        data = _read_json(self.root / EPISODES_FILE, {})
        episodes = data.get("episodes") if isinstance(data, dict) else None
        if not isinstance(episodes, list):
            return [{"number": 1, "title": "第1集", "goal": "", "hook": "", "status": "draft"}]
        cleaned: list[dict[str, Any]] = []
        for i, ep in enumerate(episodes, start=1):
            if not isinstance(ep, dict):
                continue
            cleaned.append({
                "number": int(ep.get("number") or i),
                "title": str(ep.get("title") or f"第{i}集"),
                "goal": str(ep.get("goal") or ""),
                "hook": str(ep.get("hook") or ""),
                "status": str(ep.get("status") or "draft"),
                "source_chapter": int(ep.get("source_chapter") or 0),
                "source_chapters": ep.get("source_chapters", []),
                "source_hashes": ep.get("source_hashes", {}),
                "adaptation_plan": ep.get("adaptation_plan", {}),
            })
        return sorted(cleaned, key=lambda ep: ep["number"])

    def save_episodes(self, episodes: list[dict[str, Any]]) -> None:
        _write_json(self.root / EPISODES_FILE, {"episodes": episodes, "updated_at": _now()})

    def next_adaptive_episode_number(self) -> int:
        episodes = self.load_episodes()
        for ep in episodes:
            if not ep.get("source_chapter") and not ep.get("source_chapters") and not self.has_script(ep["number"]):
                return int(ep["number"])
        return max((int(ep["number"]) for ep in episodes), default=0) + 1

    def commit_adaptive_episode(self, number: int, plan: dict, chapters: list[dict], script: str) -> None:
        """剧本成功写盘后才提交来源映射；元数据是已消费素材的权威来源。"""
        import hashlib
        episodes = self.load_episodes()
        sources = [int(c["number"]) for c in chapters]
        if sources != plan["source_chapters"]:
            raise ValueError("分集来源与选定素材不一致")
        for ep in episodes:
            existing = ep.get("source_chapters") or ([ep["source_chapter"]] if ep.get("source_chapter") else [])
            if set(existing) & set(sources):
                raise ValueError("这些章节已被其他剧集使用，请刷新待用素材")
        target = next((e for e in episodes if e["number"] == number), None)
        if target is not None and self.has_script(number):
            raise ValueError("目标剧集已有剧本，不能自动覆盖")
        target = target or {"number": number}
        target.update({"title": plan["title"], "goal": plan["goal"], "hook": plan["hook"],
                       "status": "draft", "source_chapter": sources[0], "source_chapters": sources,
                       "source_hashes": {str(c["number"]): hashlib.sha256(c["text"].encode("utf-8")).hexdigest() for c in chapters},
                       "adaptation_plan": plan})
        if not any(e["number"] == number for e in episodes):
            episodes.append(target)
        path = self.script_path(number)
        previous = path.read_text(encoding="utf-8") if path.exists() else None
        self.save_script(number, script)
        try:
            self.save_episodes(episodes)
        except Exception:
            # 保留生成稿以便人工恢复，同时回滚主剧本，避免下次续跑出现未关联的占位集。
            project_io.write_md(path.with_suffix(".pending.md"), script)
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                project_io.write_md(path, previous)
            raise

    def adaptation_state(self) -> dict:
        return _read_json(self.root / "adaptation.json", {})

    def add_episode(self, title: str = "", source_chapter: int = 0) -> dict[str, Any]:
        episodes = self.load_episodes()
        number = max((int(e["number"]) for e in episodes), default=0) + 1
        ep = {
            "number": number, "title": title or f"第{number}集", "goal": "", "hook": "",
            "status": "draft", "source_chapter": int(source_chapter or 0),
        }
        episodes.append(ep)
        self.save_episodes(episodes)
        path = self.script_path(number)
        path.write_text(script_template(number, ep["title"]), encoding="utf-8")
        return ep

    def upsert_episode_for_chapter(
        self, chapter_number: int, chapter_title: str, script_text: str
    ) -> dict[str, Any]:
        """按源章节更新/新建一集，避免同一章重复一键生成时堆出多个「第N集」。"""
        episodes = self.load_episodes()
        title = (chapter_title or f"第{chapter_number}章").strip()
        target = None
        for ep in episodes:
            if int(ep.get("source_chapter") or 0) == int(chapter_number):
                if len(ep.get("source_chapters") or []) > 1:
                    raise ValueError("本章属于跨章合并剧集，请在剧本页编辑该集，避免逐章生成覆盖分集规划")
                target = ep
                break
        if target is None:
            # 兼容旧数据：标题完全相同也视为同一章
            for ep in episodes:
                if (ep.get("title") or "").strip() == title:
                    target = ep
                    break
        if target is None:
            # 首次一键生成：复用还空着的默认模板集，避免多出一个「第1集」
            for ep in episodes:
                if int(ep.get("source_chapter") or 0) != 0:
                    continue
                current = self.load_script(int(ep["number"])).strip()
                if not current or current == script_template(
                    int(ep["number"]), ep.get("title", "")
                ).strip():
                    target = ep
                    break
        if target is None:
            target = self.add_episode(title=title, source_chapter=chapter_number)
        else:
            target["title"] = title
            target["source_chapter"] = int(chapter_number)
            self.save_episodes(episodes)
        self.save_script(int(target["number"]), script_text)
        return target

    def target_episode_number(self, chapter_number: int, chapter_title: str) -> int:
        """与保存时使用同一套匹配规则，包含首次复用默认模板的情况。"""
        episodes = self.load_episodes()
        for ep in episodes:
            if int(ep.get("source_chapter") or 0) == chapter_number:
                return int(ep["number"])
        title = (chapter_title or f"第{chapter_number}章").strip()
        for ep in episodes:
            if ep["title"].strip() == title:
                return int(ep["number"])
        for ep in episodes:
            if not ep.get("source_chapter"):
                number = int(ep["number"])
                text = self.load_script(number).strip()
                if not text or text == script_template(number, ep["title"]).strip():
                    return number
        return max((int(e["number"]) for e in episodes), default=0) + 1

    def previous_episode_tail(self, number: int, limit: int = 800) -> str:
        previous = [e for e in self.load_episodes() if int(e["number"]) < number]
        return self.load_script(int(previous[-1]["number"]))[-limit:] if previous else ""

    def has_script(self, episode: int) -> bool:
        """空模板不进入 AI 分镜任务。"""
        text = self.load_script(episode).strip()
        ep = next((e for e in self.load_episodes() if e["number"] == episode), {})
        return bool(text and text != script_template(episode, ep.get("title", "")).strip())

    def episode_report(self, episode: int) -> dict[str, Any]:
        script = self.load_script(episode)
        data = self.load_storyboard(episode)
        shots = data["shots"]
        seconds = sum(s["duration_sec"] for s in shots)
        target = float(self.load_series().get("episode_seconds") or 90)
        issues = []
        if not self.has_script(episode):
            issues.append("剧本仍为空模板")
        scenes = len(re.findall(r"^##\s+", script, re.MULTILINE))
        if not scenes:
            issues.append("缺少场次标题")
        if not shots:
            issues.append("尚未制作分镜")
        elif abs(seconds - target) > target * 0.2:
            issues.append(f"分镜总时长 {seconds:g} 秒，目标 {target:g} 秒")
        if data["script_changed"]:
            issues.append("剧本已修改，请更新分镜")
        if any(s["duration_sec"] <= 0 for s in shots):
            issues.append("存在未填写时长的镜头")
        return {"scenes": scenes, "shots": len(shots), "seconds": seconds,
                "target_seconds": target, "issues": issues}

    def export_scripts(self, episode: int | None = None) -> Path:
        episodes = [e for e in self.load_episodes() if episode is None or e["number"] == episode]
        if not episodes:
            raise ValueError("没有可导出的剧集")
        name = episode_filename(episode) if episode is not None else "all_episodes"
        path = self.exports_dir / f"{name}_scripts.md"
        project_io.write_md(path, "\n\n---\n\n".join(self.load_script(e["number"]).strip() for e in episodes) + "\n")
        return path

    def delete_episode(self, number: int) -> None:
        episodes = [e for e in self.load_episodes() if int(e["number"]) != int(number)]
        self.save_episodes(episodes)
        for path in (self.script_path(number), self.storyboard_path(number)):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def import_chapter_as_episode(
        self, chapter_number: int, chapter_title: str, chapter_text: str
    ) -> dict[str, Any]:
        """把一章小说原文落成一集剧本素材（确定性兜底，AI 改编走 worker）。"""
        title = (chapter_title or f"第{chapter_number}章").strip()
        body = (chapter_text or "").strip()
        script = (
            f"# 第{chapter_number}集 {title}\n\n"
            f"> 来源：小说第{chapter_number}章。下面是原文素材，下一步改成场次/动作/对白。\n\n"
            f"## 第1场 {title} · 日/夜\n\n"
            "（把小说里的心理描写翻译成观众能看见的动作。）\n\n"
            f"{body}\n"
        )
        return self.upsert_episode_for_chapter(chapter_number, title, script)

    def script_path(self, episode: int) -> Path:
        return self.episodes_dir / f"{episode_filename(episode)}.md"

    def load_script(self, episode: int) -> str:
        path = self.script_path(episode)
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("读取剧本失败 %s: %s", path, e)
            return ""

    def save_script(self, episode: int, text: str) -> None:
        path = self.script_path(episode)
        if path.exists():
            previous = path.read_text(encoding="utf-8")
            if previous != (text or ""):
                project_io.write_md(path.with_suffix(".bak.md"), previous)
        project_io.write_md(path, text or "")

    def script_hash(self, episode: int) -> str:
        """当前剧本文本 hash；用于判断分镜是否过期。"""
        try:
            text = self.load_script(episode)
        except Exception:
            return ""
        return hashlib.sha256((text or "").encode("utf-8")).hexdigest()

    def update_episode_meta(self, number: int, **fields) -> None:
        episodes = self.load_episodes()
        for ep in episodes:
            if int(ep["number"]) == int(number):
                for key in ("title", "goal", "hook", "status"):
                    if key in fields:
                        ep[key] = fields[key]
                break
        self.save_episodes(episodes)

    # ── 分镜 ──

    def storyboard_path(self, episode: int) -> Path:
        return self.storyboard_dir / f"{episode_filename(episode)}.json"

    def load_storyboard(self, episode: int) -> dict[str, Any]:
        episodes = {int(e["number"]): e for e in self.load_episodes()}
        ep = episodes.get(int(episode), {"number": episode, "title": f"第{episode}集"})
        series = self.load_series()
        data = _read_json(self.storyboard_path(episode), {})
        if not isinstance(data, dict):
            data = {}
        shots = data.get("shots")
        shots = shots if isinstance(shots, list) else []
        normalized = [normalize_shot(s, episode, i) for i, s in enumerate(shots, start=1)]
        source_hash = str(data.get("source_script_hash") or "")
        current_hash = self.script_hash(episode)
        return {
            "schema_version": SCHEMA_VERSION,
            "episode": int(episode),
            "title": str(data.get("title") or ep.get("title") or f"第{episode}集"),
            "aspect": str(data.get("aspect") or series.get("aspect") or "9:16"),
            "duration_sec": data.get("duration_sec", series.get("episode_seconds", 90)),
            "source_script_hash": source_hash,
            "script_changed": bool(source_hash and current_hash and source_hash != current_hash),
            "updated_at": str(data.get("updated_at") or ""),
            "shots": normalized,
        }

    def save_storyboard(
        self, episode: int, data: dict[str, Any], update_source: bool = False
    ) -> dict[str, Any]:
        current = self.load_storyboard(episode)
        shots = data.get("shots") if isinstance(data, dict) else None
        current["shots"] = [
            normalize_shot(s, episode, i)
            for i, s in enumerate(shots if isinstance(shots, list) else [], start=1)
        ]
        for key in ("title", "aspect", "duration_sec"):
            if isinstance(data, dict) and data.get(key) not in (None, ""):
                current[key] = data[key]
        # 只有“按剧本生成”才刷新源 hash；手工改表格不会把过期标记洗掉
        if update_source:
            current["source_script_hash"] = self.script_hash(episode)
        current["schema_version"] = SCHEMA_VERSION
        current["updated_at"] = _now()
        current.pop("script_changed", None)
        _write_json(self.storyboard_path(episode), current)
        return current

    def shots_from_script(self, script: str, episode: int) -> list[dict[str, Any]]:
        """把剧本里 ``## 场次`` 标题拆成一镜一场的草稿。

        只做确定性骨架：地点/时间/人物/动作/台词粗提取，不替代 AI 分镜。
        """
        if not script:
            return []
        lines = script.replace("\r\n", "\n").splitlines()
        headings: list[tuple[int, str]] = []
        for i, line in enumerate(lines):
            text = line.strip()
            if not text:
                continue
            if re.match(r"^#{2,4}\s+\S", text):
                headings.append((i, re.sub(r"^#{2,4}\s+", "", text)))
            elif re.match(r"^第\s*[0-9一二三四五六七八九十百零两]+\s*场", text):
                headings.append((i, text))
            elif re.match(r"^场\s*[0-9一二三四五六七八九十百零两]+\s*[：:]", text):
                headings.append((i, text))
        if not headings:
            return []

        shots: list[dict[str, Any]] = []
        for idx, (line_no, heading) in enumerate(headings[:100], start=1):
            end = headings[idx][0] if idx < len(headings) else len(lines)
            body = [ln.strip() for ln in lines[line_no + 1:end] if ln.strip()]
            clean_heading = re.sub(
                r"^第\s*[0-9一二三四五六七八九十百零两]+\s*场\s*[：:、.]?\s*", "", heading
            )
            clean_heading = re.sub(
                r"^场\s*[0-9一二三四五六七八九十百零两]+\s*[：:、.]?\s*", "", clean_heading
            ).strip()
            location, time = clean_heading, ""
            parts = re.split(r"\s*[·|｜]\s*", clean_heading, maxsplit=1)
            if len(parts) == 2:
                location, time = parts[0], parts[1]
            characters: list[str] = []
            action_parts: list[str] = []
            dialogue_parts: list[str] = []
            for ln in body:
                m = re.match(r"^([^：:]{1,12})[：:]\s*(.+)$", ln)
                if m and not m.group(1).startswith(("画面", "动作", "旁白", "字幕", "备注")):
                    speaker, line_text = m.group(1).strip(), m.group(2).strip()
                    if speaker not in characters:
                        characters.append(speaker)
                    dialogue_parts.append(f"{speaker}：{line_text}")
                else:
                    action_parts.append(ln)
            shot = new_shot(episode, idx)
            shot.update({
                "scene_id": f"SC{idx:02d}",
                "location": location.strip(),
                "time": time.strip(),
                "characters": "、".join(characters),
                "action": " ".join(action_parts)[:300],
                "dialogue": " ".join(dialogue_parts)[:300],
                "status": "draft",
            })
            shots.append(shot)
        return shots

    # ── 资产 ──

    def load_assets(self) -> list[dict[str, Any]]:
        data = _read_json(self.assets_dir / ASSETS_FILE, {})
        assets = data.get("assets") if isinstance(data, dict) else None
        if not isinstance(assets, list):
            return []
        cleaned: list[dict[str, Any]] = []
        for i, asset in enumerate(assets, start=1):
            if not isinstance(asset, dict):
                continue
            notes = str(asset.get("notes") or "")
            source = str(asset.get("source") or "")
            if not source:
                # 老数据没有 source：按 notes 标记回推
                source = _ASSET_SOURCE_ALIASES.get(notes, "manual")
            cleaned.append({
                "kind": str(asset.get("kind") or "character"),
                "id": str(asset.get("id") or f"asset_{i:03d}"),
                "name": str(asset.get("name") or ""),
                "source": source,
                "prompt": str(asset.get("prompt") or ""),
                "ref": str(asset.get("ref") or ""),
                "status": str(asset.get("status") or "draft"),
                "notes": notes,
                # 与小说正典的对应关系：人物/地点/道具的名称或角色 id
                "entity": str(asset.get("entity") or ""),
                # 已生成图片的相对路径（assets/generated/...）
                "image": str(asset.get("image") or ""),
            })
        return cleaned

    def save_assets(self, assets: list[dict[str, Any]]) -> None:
        _write_json(self.assets_dir / ASSETS_FILE, {"assets": assets, "updated_at": _now()})

    def extract_assets_from_storyboard(self, episode: int) -> int:
        """从分镜的角色/地点列提取资产占位，返回新增数量。

        能对上小说正典的（名字或别名）标 `source=canon` 并回填 entity；
        对不上的（龙套、临时场景）标 `source=storyboard`，属于短剧原创资产。
        """
        assets = self.load_assets()
        existing = {
            (a["kind"], _normalize_asset_name(a["name"]))
            for a in assets if a["name"].strip()
        }
        added = 0

        def add_asset(kind: str, name: str) -> None:
            nonlocal added
            name = name.strip()
            norm = _normalize_asset_name(name)
            if not norm or (kind, norm) in existing:
                return
            matched = self.match_canon_entity(kind, name)
            assets.append({
                "kind": kind,
                "id": _unique_asset_id(assets, kind, name),
                "name": name,
                "source": "canon" if matched else "storyboard",
                "prompt": "",
                "ref": "",
                "status": "draft",
                "notes": "从分镜提取·对上正典" if matched else "从分镜自动提取（短剧原创）",
                "entity": (matched.get("entity_id") or matched["name"]) if matched else "",
                "image": "",
            })
            existing.add((kind, norm))
            added += 1

        for shot in self.load_storyboard(episode)["shots"]:
            for name in re.split(r"[、,，/／+＋\s]+", str(shot.get("characters") or "")):
                add_asset("character", name)
            add_asset("location", str(shot.get("location") or ""))
        if added:
            self.save_assets(assets)
        return added

    # ── 资产 ↔ 小说正典 ──

    def canon_entities(self, kinds=("character", "location", "prop")) -> list[dict[str, Any]]:
        """小说侧可对应成资产的实体：人物卡 / 世界状态地点 / 物品图鉴。"""
        out: list[dict[str, Any]] = []
        if "character" in kinds:
            data = _read_json(self.project_dir / "canon" / "characters.json", {})
            for rec in data.get("characters") or []:
                if not isinstance(rec, dict):
                    continue
                name = str(rec.get("name") or "").strip()
                if not name:
                    continue
                profile = rec.get("public_profile") or {}
                summary = "；".join(
                    str(v).strip() for v in profile.values()
                    if isinstance(v, str) and v.strip()
                )[:200]
                out.append({
                    "kind": "character", "name": name,
                    "summary": summary, "entity_id": str(rec.get("id") or ""),
                    "aliases": [str(a).strip() for a in (rec.get("aliases") or [])
                                if str(a).strip()],
                })
        world = _read_json(self.project_dir / "world_state.json", {})
        if "location" in kinds:
            for name, info in (world.get("locations") or {}).items():
                out.append({"kind": "location", "name": str(name),
                            "summary": _entity_summary(info), "entity_id": ""})
        if "prop" in kinds:
            for name, info in (world.get("items_catalog") or {}).items():
                out.append({"kind": "prop", "name": str(name),
                            "summary": _entity_summary(info), "entity_id": ""})
        return out

    def _canon_index(self, kinds=("character", "location", "prop")) -> dict[tuple[str, str], dict]:
        """(类型, 名称/别名) → 正典实体，用来把分镜里的名字对回小说。"""
        index: dict[tuple[str, str], dict] = {}
        for ent in self.canon_entities(kinds):
            index[(ent["kind"], ent["name"])] = ent
            for alias in ent.get("aliases") or []:
                index.setdefault((ent["kind"], alias), ent)
        return index

    def match_canon_entity(self, kind: str, name: str) -> dict[str, Any] | None:
        """分镜里的角色/地点名 → 正典实体（支持别名、括号注解、包含匹配）。"""
        clean = _normalize_asset_name(name)
        if not clean:
            return None
        index = self._canon_index((kind,))
        if (kind, clean) in index:
            return index[(kind, clean)]
        for (k, alias), ent in index.items():
            if k != kind or not alias:
                continue
            if alias in clean or clean in alias:
                return ent
        return None

    def sync_assets_from_canon(self, kinds=("character", "location"), limit: int = 0) -> int:
        """把小说里的人物/地点补进资产表（按 类型+名称 去重，不覆盖已有行）。"""
        assets = self.load_assets()
        existing = {
            (a["kind"], _normalize_asset_name(a["name"]))
            for a in assets if a["name"].strip()
        }
        added = 0
        for ent in self.canon_entities(kinds):
            key = (ent["kind"], _normalize_asset_name(ent["name"]))
            if key in existing:
                continue
            assets.append({
                "kind": ent["kind"],
                "id": _unique_asset_id(assets, ent["kind"], ent["name"]),
                "name": ent["name"],
                "source": "canon",
                "prompt": "",
                "ref": "",
                "status": "draft",
                "notes": "从小说导入",
                "entity": ent.get("entity_id") or ent["name"],
                "image": "",
            })
            existing.add(key)
            added += 1
            if limit and added >= limit:
                break
        if added:
            self.save_assets(assets)
        return added

    def check_assets(self) -> dict[str, Any]:
        """资产体检：来源分布、缺提示词/图、正典失联、重名。

        「短剧原创」（source=storyboard，比如龙套、临时场景）不算错误，
        但会单独计数——它们是小说里没有、短剧自己需要的资产。
        """
        assets = self.load_assets()
        entities = self.canon_entities(("character", "location", "prop"))
        canon_ids = {str(e.get("entity_id") or ""): e for e in entities if e.get("entity_id")}
        canon_names: dict[str, set[str]] = {}
        for ent in entities:
            canon_names.setdefault(ent["kind"], set()).add(_normalize_asset_name(ent["name"]))
        report: dict[str, Any] = {
            "total": len(assets), "canon": 0, "original": 0, "manual": 0,
            "missing_prompt": 0, "missing_image": 0,
            "orphan": [], "duplicates": [],
        }
        seen: set[tuple[str, str]] = set()
        for asset in assets:
            source = asset.get("source") or "manual"
            bucket = "canon" if source == "canon" else ("original" if source == "storyboard" else "manual")
            report[bucket] += 1
            if not asset.get("prompt"):
                report["missing_prompt"] += 1
            if not asset.get("image"):
                report["missing_image"] += 1
            name_key = (asset["kind"], _normalize_asset_name(asset["name"]))
            if name_key in seen:
                report["duplicates"].append(asset["name"])
            seen.add(name_key)
            if source != "canon":
                continue
            entity = str(asset.get("entity") or "")
            if entity in canon_ids:
                continue
            if _normalize_asset_name(asset["name"]) in canon_names.get(asset["kind"], set()):
                continue
            report["orphan"].append(asset["name"])
        return report

    def asset_prompt(self, asset: dict[str, Any]) -> tuple[str, str]:
        """给一个资产生成 (正向提示词, 负向提示词)；已手填的提示词优先。"""
        custom = str(asset.get("prompt") or "").strip()
        if custom:
            return custom, ASSET_NEGATIVE
        name = str(asset.get("name") or "").strip()
        kind = str(asset.get("kind") or "character")
        label = ASSET_KIND_LABELS.get(kind, "资产")
        series = self.load_series()
        meta = _read_json(self.project_dir / "meta.json", {})
        genre = str(meta.get("genre") or series.get("genre") or "")
        style = str(meta.get("style") or series.get("style") or "")
        desc = ""
        for ent in self.canon_entities((kind,)):
            if ent["name"] == name and ent.get("summary"):
                desc = ent["summary"]
                break
        if not desc:
            notes = str(asset.get("notes") or "")
            if notes and notes not in ("从分镜自动提取", "从小说导入"):
                desc = notes
        parts = [f"{name}，{label}设定参考图"]
        if desc:
            parts.append(desc)
        if genre:
            parts.append(f"{genre}题材")
        if style:
            parts.append(f"{style}风格")
        parts.append("竖屏短剧，写实电影感，柔和布光，背景干净，高清细节，正面参考")
        positive = "，".join(parts)
        negative = ASSET_NEGATIVE
        return positive, negative

    def save_generated_image(self, asset_id: str, data: bytes, ext: str = ".png") -> str:
        """把生成图落到 assets/generated/，回填路径并标记 ready；返回相对路径。"""
        folder = self.assets_dir / "generated"
        folder.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w\-]+", "_", asset_id) or "asset"
        path = folder / f"{safe}{ext}"
        path.write_bytes(data)
        rel = f"generated/{path.name}"
        assets = self.load_assets()
        for asset in assets:
            if asset["id"] == asset_id:
                asset["image"] = rel
                asset["ref"] = rel
                asset["status"] = "ready"
        self.save_assets(assets)
        return rel

    # ── 导出 ──

    def export_storyboard_csv(self, episode: int | None = None) -> Path:
        """导出分镜表 CSV（UTF-8 BOM，Excel 可直接打开）。"""
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        if episode is None:
            filename = "分镜_全剧.csv"
            rows: list[dict[str, Any]] = []
            for ep in self.load_episodes():
                data = self.load_storyboard(int(ep["number"]))
                for shot in data["shots"]:
                    rows.append({"episode": int(ep["number"]), **shot})
        else:
            filename = f"分镜_第{int(episode):03d}集.csv"
            data = self.load_storyboard(int(episode))
            rows = [{"episode": int(episode), **shot} for shot in data["shots"]]
        path = self.exports_dir / filename
        with path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["集数"] + [label for _key, label in SHOT_COLUMNS])
            for row in rows:
                values = [row.get("episode", "")]
                for key, _label in SHOT_COLUMNS:
                    value = row.get(key, "")
                    if key == "duration_sec":
                        try:
                            value = f"{float(value):.1f}"
                        except (TypeError, ValueError):
                            value = ""
                    values.append(value)
                writer.writerow(values)
        return path

    def export_storyboard_json(self, episode: int | None = None) -> Path:
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        if episode is None:
            payload = {
                "schema_version": SCHEMA_VERSION,
                "series": self.load_series(),
                "episodes": [
                    self.load_storyboard(int(ep["number"])) for ep in self.load_episodes()
                ],
            }
            path = self.exports_dir / "分镜_全剧.json"
        else:
            payload = self.load_storyboard(int(episode))
            path = self.exports_dir / f"分镜_第{int(episode):03d}集.json"
        _write_json(path, payload)
        return path
