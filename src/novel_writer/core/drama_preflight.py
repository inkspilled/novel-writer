"""短剧开工参数、工具自检和总导演提示词。"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from . import project_io
from .logger import get_logger

logger = get_logger(__name__)

PREFLIGHT_FILE = "preflight.md"
DIRECTOR_PROMPT_FILE = "00_总导演开工提示词.md"

DIRECTOR_PROMPT = """你当这支短片的总导演，全程负责，我只和你沟通。
开工前先做两件事：
1. 问我：片长、画幅、要不要配乐和字幕。
2. 检查 Codex、ffmpeg、Python 和 ARK_API_KEY 能不能用，缺什么告诉我。

记住所有文件都放在一个新的项目文件夹里。

分工：
- 你：写故事和分镜，写所有提示词，调用其他模型，抽帧放大检查每张图和每段视频，用 ffmpeg 和 Python 写代码做后期。
- Codex（画师）：画设定图、样帧和每个镜头的首尾帧。你不自己画。

如果你就是 Codex，就用你自己的生图工具。

- Seedance（动画师）：把首尾帧变成几秒钟的视频。

- 我：选方向、确认画风、看动态、听声音。

调用 Codex：
codex exec --skip-git-repo-check -s read-only -i 参考图.png - < 提示词.txt
提示词开头写：Use your image generation tool to create exactly ONE image. Do not run shell commands.
画好的图在 ~/.codex/generated_images/<日志里的 session id>/。

调用 Seedance：
POST https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks
key 在环境变量 ARK_API_KEY 里，不要打印出来。请求格式查火山方舟文档。
模型选 doubao-seedance-2-5-260628 或 doubao-seedance-2-0-mini-260615。

总原则：文字里能发现的错，不留到图上；图上能发现的错，不留到视频上。

规矩：
1. 开始整集制作时，先确认故事再生成正式镜头。
2. 我确认画风前，不画正式镜头。
3. 提交视频生成前，另开一个 agent 审核提示词和图片的每个细节，它只挑错，不改文件。审核通过后提交。
4. 视频有问题，先想能不能用代码修，修不了才重拍，只拍这一镜。
5. 新版本存新文件，不覆盖、不删除旧文件。
6. 你看不到真正的动态，也听不到声音。没看过、没听过的东西，要明确告诉我。
7. 视频服务失败时停止本次生成，保留已有的片段。"""

# 七条规矩单独留一份，审核/代理/监督都按这个口径判
RULES: tuple[str, ...] = (
    "开始整集制作时，先确认故事再生成正式镜头。",
    "我确认画风前，不画正式镜头。",
    "提交视频生成前，另开一个 agent 审核提示词和图片的每个细节，它只挑错，不改文件。审核通过后提交。",
    "视频有问题，先想能不能用代码修，修不了才重拍，只拍这一镜。",
    "新版本存新文件，不覆盖、不删除旧文件。",
    "你看不到真正的动态，也听不到声音。没看过、没听过的东西，要明确告诉我。",
    "视频服务失败时停止本次生成，保留已有的片段。",
)

# 开工参数
INTAKE_FIELDS: tuple[tuple[str, str], ...] = (
    ("episode_seconds", "片长：单集多少秒？"),
    ("aspect", "画幅：竖屏还是横屏、几比几？"),
    ("music", "配乐和字幕：要不要配歌（歌名/时长/BPM）、要不要字幕？"),
)


def intake_questions(series: dict[str, Any]) -> list[str]:
    """开工参数里还没定死的那些；全部答完返回空列表。"""
    series = series or {}
    missing: list[str] = []
    if int(series.get("episode_seconds") or 0) <= 0:
        missing.append(INTAKE_FIELDS[0][1])
    if not str(series.get("aspect") or "").strip():
        missing.append(INTAKE_FIELDS[1][1])
    intake = series.get("intake") if isinstance(series.get("intake"), dict) else {}
    if not intake.get("music_answered"):
        missing.append(INTAKE_FIELDS[2][1])
    return missing


def _check(status: str, label: str, detail: str) -> dict[str, str]:
    return {"id": label.split("：")[0], "label": label, "status": status, "detail": detail}


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def check_environment(
    cfg: dict | None = None, *, series: dict | None = None, ffmpeg: str | None = None
) -> list[dict[str, str]]:
    """逐项自检；只报状态，不打印任何密钥。"""
    if cfg is None:
        from .app_config import load_config

        cfg = load_config()
    series = series or {}
    checks: list[dict[str, str]] = []

    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info >= (3, 10):
        checks.append(_check("ok", "Python", f"{version}（≥3.10）"))
    else:
        checks.append(_check("missing", "Python", f"{version} 太低，需要 ≥3.10"))

    ffmpeg_bin = ffmpeg if ffmpeg is not None else shutil.which("ffmpeg")
    if ffmpeg_bin:
        checks.append(_check("ok", "ffmpeg", str(ffmpeg_bin)))
    else:
        checks.append(_check(
            "missing", "ffmpeg",
            "没装 ffmpeg：后期转场/调色/BT.601→709/放大/卡点/切点抽帧全部不可用",
        ))

    ffprobe_bin = shutil.which("ffprobe")
    checks.append(
        _check("ok", "ffprobe", str(ffprobe_bin)) if ffprobe_bin
        else _check("warn", "ffprobe", "没装 ffprobe：读时长会退化成解析 ffmpeg 输出")
    )

    checks.append(
        _check("ok", "Pillow", "可画 1920×1080 色块草图和黑白对比检查")
        if _module_available("PIL")
        else _check("missing", "Pillow", "没装 Pillow：色块草图/裁切推近/局部贴回不可用")
    )

    # ARK_API_KEY：只报有没有，值绝不外泄
    key_present = bool((os.environ.get("ARK_API_KEY") or "").strip())
    if not key_present:
        section = cfg.get("video_generation") or {}
        key_present = bool(str(section.get("api_key") or "").strip())
    checks.append(
        _check("ok", "ARK_API_KEY", "已设置（值不打印）") if key_present
        else _check("warn", "ARK_API_KEY", "未设置：云端 Seedance 不可用，可先用 ComfyUI 本地出片")
    )

    codex_bin = shutil.which("codex")
    checks.append(
        _check("ok", "Codex CLI", str(codex_bin)) if codex_bin
        else _check("warn", "Codex CLI", "未安装：可改用 ComfyUI / 图片 API 当画师")
    )

    from .image_generation import configuration_error as image_error
    from .image_generation import config_from_app as image_config
    from .video_generation import configuration_error as video_error
    from .video_generation import config_from_app as video_config

    image_cfg = image_config(cfg)
    image_problem = image_error(image_cfg)
    checks.append(
        _check("ok", "图片服务", str(image_cfg.get("provider") or "comfyui"))
        if not image_problem
        else _check("missing", "图片服务", image_problem)
    )

    video_cfg = video_config(cfg)
    video_problem = video_error(video_cfg)
    checks.append(
        _check("ok", "视频服务", str(video_cfg.get("provider") or "comfyui"))
        if not video_problem
        else _check("missing", "视频服务", video_problem)
    )

    checks.append(
        _check("ok", "配色", str(series.get("palette") or "")[:40])
        if str(series.get("palette") or "").strip()
        else _check("warn", "配色", "还没定：画第一张图之前必须定死，否则全片会漂")
    )
    checks.append(
        _check("ok", "画风", str(series.get("style") or "")[:40])
        if str(series.get("style") or "").strip()
        else _check("warn", "画风", "还没定：确认画风之前不画正式镜头")
    )
    return checks


def missing_checks(checks: list[dict[str, str]]) -> list[dict[str, str]]:
    return [item for item in checks if item.get("status") == "missing"]


def check_lines(checks: list[dict[str, str]]) -> str:
    marks = {"ok": "✅", "warn": "⚠️", "missing": "❌"}
    lines = []
    for item in checks:
        lines.append(
            f"{marks.get(item.get('status', ''), '·')} {item.get('label')}：{item.get('detail')}"
        )
    return "\n".join(lines)


def build_director_prompt(
    series: dict[str, Any] | None = None,
    checks: list[dict[str, str]] | None = None,
    *,
    project_name: str = "",
    project_dir: str | Path | None = None,
) -> str:
    """文章原文 + 本机自检 + 文件落点，直接可发给导演模型。"""
    series = series or {}
    if checks is None:
        checks = check_environment(series=series)
    root = Path(project_dir) if project_dir else None
    title = project_name or str(series.get("title") or "这部短片")

    lines = [
        f"# 总导演开工提示词 · {title}",
        "",
        DIRECTOR_PROMPT,
        "",
        "---",
        "",
        "## 本机自检结果（刚跑的，缺什么按上面第 2 条告诉我）",
        "",
        check_lines(checks),
        "",
        "## 这部片子的开工参数",
        "",
        f"- 片长：{int(series.get('episode_seconds') or 0) or '待定'} 秒/集",
        f"- 画幅：{series.get('aspect') or '待定'}",
        f"- 配乐：{series.get('music') or '不配乐'}；字幕：{'要' if series.get('subtitles') else '不要'}",
        "",
        "## 文件落点（所有新文件都在这里，不覆盖旧文件）",
        "",
    ]
    if root is not None:
        lines += [
            f"- 项目目录：{root}",
            f"- 剧本：{root / 'drama' / 'episodes'}",
            f"- 分镜：{root / 'drama' / 'storyboard'}",
            f"- 首尾帧：{root / 'drama' / 'keyframes'}",
            f"- 片段：{root / 'drama' / 'videos'}",
            f"- 审核记录：{root / 'drama' / 'review'}",
            f"- 导出：{root / 'drama' / 'exports'}",
            "",
            "总结、复盘、经验都写进 `drama/retro/`，每做一集追加一条。",
        ]
    else:
        lines.append("- 项目目录：待定")
    lines += [
        "",
        "## 我确认的边界",
        "",
        "- 我看不到真正的动态、听不到声音；没看过、没听过的东西你要明确说。",
    ]
    return "\n".join(lines) + "\n"


def build_preflight_md(
    series: dict[str, Any] | None = None,
    checks: list[dict[str, str]] | None = None,
    *,
    project_dir: str | Path | None = None,
) -> str:
    """自检报告：开工参数 + 环境 + 下一步。"""
    series = series or {}
    checks = checks if checks is not None else check_environment(series=series)
    questions = intake_questions(series)
    lines = [
        "# 开工自检",
        "",
        "> 对应文章「开工前先做两件事」：确认开工参数、检查工具能不能用。",
        "",
        "## 一、开工参数",
        "",
    ]
    for _key, question in INTAKE_FIELDS:
        lines.append("- [ ] " + question)
    if not questions:
        lines.append("")
        lines.append("开工参数已确认 ✅")
    lines += ["", "## 二、工具自检", "", check_lines(checks), "", "## 三、下一步", ""]
    missing = missing_checks(checks)
    if missing:
        lines.append("先补齐这些，再开画：")
        lines += [f"- ❌ {item.get('label')}：{item.get('detail')}" for item in missing]
    else:
        lines.append("工具齐了，可以往下走：故事定稿 → 分镜 → 画风 → 首尾帧 → 视频提示词 → 生成。")
    if project_dir is not None:
        lines += ["", f"项目目录：`{Path(project_dir)}`"]
    return "\n".join(lines) + "\n"


def write_preflight(
    store, series: dict[str, Any] | None = None, *, cfg: dict | None = None
) -> dict[str, Any]:
    """把自检报告和总导演提示词写进 `drama/exports/`，返回结果摘要。"""
    series = series if series is not None else store.load_series()
    checks = check_environment(cfg, series=series)
    exports = store.exports_dir
    exports.mkdir(parents=True, exist_ok=True)
    report_path = exports / PREFLIGHT_FILE
    prompt_path = exports / DIRECTOR_PROMPT_FILE
    project_io.write_md(report_path, build_preflight_md(series, checks, project_dir=store.project_dir))
    project_io.write_md(
        prompt_path,
        build_director_prompt(
            series, checks,
            project_name=str(series.get("title") or store.project_dir.name),
            project_dir=store.project_dir,
        ),
    )
    questions = intake_questions(series)
    missing = missing_checks(checks)
    return {
        "report": report_path,
        "prompt": prompt_path,
        "checks": checks,
        "missing": missing,
        "questions": questions,
        "summary": (
            f"自检完成：{len(missing)} 项缺失、{len(questions)} 个问题待答；"
            f"已写 {report_path.name} 和 {prompt_path.name}"
        ),
    }
