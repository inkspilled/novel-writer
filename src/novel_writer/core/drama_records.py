"""统一生成账本：出图/出视频各落一条记录，失败项单独成清单，旧版本建索引。

三份文件都在 ``drama/`` 根目录（append-only，只加不改历史）：

- ``generation_records.jsonl``：回答「这张图/这段视频是怎么来的」——
  工作流文件及其 hash、seed、提示词与负面词摘要、参考图 digest、输出文件 digest。
  seed 本来就是确定性算法（``asset_seed``），账本把它显式化，换台机器也能复现；
- ``versions_index.jsonl``：旧图/旧分镜移入 ``versions/`` 时登记
  （属主、旧文件、被谁替换）。没有索引的时间戳文件名对不上号；
- ``failures.json``：本阶段失败的项（阶段、目标、原因）。
  UI 显示失败数，「重试失败项」只重跑这些目标，已成功的靠增量跳过不动。

core 层无 Qt；查询接口给 UI 和复盘用。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)

RECORDS_FILE = "generation_records.jsonl"
VERSIONS_INDEX_FILE = "versions_index.jsonl"
FAILURES_FILE = "failures.json"
_MAX_FAILURES = 500
_PROMPT_CAP = 300


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _drama_root(store) -> Path:
    """DramaStore 或直接的 drama 目录都接受。"""
    root = getattr(store, "root", store)
    return Path(root)


# ── 生成账本 ──


def records_path(store) -> Path:
    return _drama_root(store) / RECORDS_FILE


def _client_meta(client) -> tuple[str, str, str]:
    """从客户端对象提取 provider / 工作流路径 / 模型名；取不到就留空。"""
    if client is None:
        return "", "", ""
    provider = client.__class__.__name__
    workflow = str(getattr(client, "workflow_path", "") or "")
    model = ""
    cfg = getattr(client, "cfg", None)
    if isinstance(cfg, dict):
        provider = str(cfg.get("provider") or provider)
        workflow = str(cfg.get("workflow") or workflow)
        model = str(cfg.get("model") or "")
    return provider, workflow, model


def append_generation_record(store, record: dict[str, Any]) -> None:
    path = records_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({**record, "time": record.get("time") or _now()},
                      ensure_ascii=False, default=str)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def record_generation(
    store,
    *,
    target: str,
    client=None,
    kind: str = "image",
    seed: int | None = None,
    positive: str = "",
    negative: str = "",
    width: int = 0,
    height: int = 0,
    reference: str | Path = "",
    output: str | Path = "",
) -> dict[str, Any]:
    """记一条生成记录；在生成成功、文件已落盘之后调用。

    ``target`` 是稳定标识：资产 ``asset:{id}:{view}``、首尾帧 ``keyframe:{ep}:{shot}:{which}``、
    片段 ``clip:{ep}:{shot}``。digest 复用 drama_production 的口径。
    """
    from .drama_production import digest, file_digest

    provider, workflow, model = _client_meta(client)
    output_path = Path(output) if output else None
    reference_text = str(reference or "")
    record: dict[str, Any] = {
        "time": _now(),
        "target": str(target),
        "kind": kind,
        "provider": provider,
        "model": model,
        "workflow": workflow,
        "workflow_hash": file_digest(Path(workflow)) if workflow else "",
        "seed": int(seed) if seed is not None else None,
        "prompt": (positive or "")[:_PROMPT_CAP],
        "prompt_digest": digest(positive or ""),
        "negative_digest": digest(negative or ""),
        "width": int(width or 0),
        "height": int(height or 0),
        "reference": reference_text,
        "reference_digest": (file_digest(Path(reference_text))
                             if reference_text and Path(reference_text).is_file() else ""),
        "output": str(output or ""),
        "output_digest": file_digest(output_path) if output_path else "",
    }
    try:
        append_generation_record(store, record)
    except OSError as e:  # 账本写失败不拦生成本身
        logger.warning("生成记录写入失败 %s: %s", records_path(store), e)
    return record


def read_generation_records(
    store, *, target: str | None = None, limit: int = 0
) -> list[dict[str, Any]]:
    """读生成记录；target 给了就只看这个目标，limit 取最近的 N 条。"""
    path = records_path(store)
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and (target is None or data.get("target") == target):
                entries.append(data)
    except OSError:
        return []
    if limit and len(entries) > limit:
        entries = entries[-limit:]
    return entries


def latest_record(store, target: str) -> dict[str, Any] | None:
    records = read_generation_records(store, target=target)
    return records[-1] if records else None


def record_markdown(store, *, target: str | None = None, limit: int = 50) -> str:
    """人看的记录表（预览对话框 / 复盘用）。"""
    entries = read_generation_records(store, target=target, limit=limit)
    if not entries:
        return "没有生成记录。"
    lines = [
        "# 生成记录",
        "",
        f"> 共 {len(entries)} 条" + (f"（target={target}）" if target else "") + "。",
        "",
        "| 时间 | 目标 | 服务 | 工作流 | seed | 输出 |",
        "|---|---|---|---|---|---|",
    ]
    for item in entries:
        lines.append(
            "| {t} | {target} | {provider} | `{wf}` | {seed} | `{out}` |".format(
                t=str(item.get("time") or "")[:19],
                target=str(item.get("target") or ""),
                provider=str(item.get("provider") or item.get("model") or ""),
                wf=Path(str(item.get("workflow") or "")).name if item.get("workflow") else "",
                seed="" if item.get("seed") is None else item.get("seed"),
                out=Path(str(item.get("output") or "")).name if item.get("output") else "",
            )
        )
    return "\n".join(lines) + "\n"


# ── 失败项清单 ──


def failures_path(store) -> Path:
    return _drama_root(store) / FAILURES_FILE


def load_failures(store) -> list[dict[str, Any]]:
    path = failures_path(store)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = data.get("items") if isinstance(data, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def save_failures(store, items: list[dict[str, Any]]) -> None:
    path = failures_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    from . import project_io

    project_io.write_json(path, {"updated_at": _now(), "items": items[:_MAX_FAILURES]})


def record_failure(store, *, stage: str, target: str, label: str = "",
                   message: str = "") -> list[dict[str, Any]]:
    """登记一个失败项；同一 (stage, target) 只留最新一条，并移到队尾。"""
    items = [item for item in load_failures(store)
             if not (item.get("stage") == stage and item.get("target") == target)]
    items.append({
        "stage": str(stage),
        "target": str(target),
        "label": str(label or target),
        "message": str(message or "")[:500],
        "time": _now(),
    })
    save_failures(store, items)
    return items


def clear_failures(store, *, stage: str | None = None,
                   targets: list[str] | None = None) -> list[dict[str, Any]]:
    """清掉失败项：整个阶段、或指定目标（重试成功后调用）。都给则取交集。"""
    items = load_failures(store)
    if stage is not None:
        items = [item for item in items if item.get("stage") != stage]
    if targets is not None:
        keep = {str(t) for t in targets}
        items = [item for item in items if item.get("target") not in keep]
    save_failures(store, items)
    return items


def failures_summary(store, stage: str | None = None) -> str:
    items = load_failures(store)
    if stage is not None:
        items = [item for item in items if item.get("stage") == stage]
    if not items:
        return ""
    heads = "、".join(str(item.get("label") or item.get("target")) for item in items[:3])
    more = f" 等 {len(items)} 项" if len(items) > 3 else ""
    return f"失败 {len(items)} 项：{heads}{more}"


# ── versions/ 索引 ──


def versions_index_path(store) -> Path:
    return _drama_root(store) / VERSIONS_INDEX_FILE


def index_version(store, *, file: str | Path, owner: str,
                  replaced_by: str = "", note: str = "") -> None:
    """旧版本文件进 versions/ 时登记一行；file 是相对 drama 根的路径。"""
    path = versions_index_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "time": _now(),
        "owner": str(owner),
        "file": Path(file).as_posix(),
        "replaced_by": str(replaced_by),
        "note": str(note),
    }
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.warning("版本索引写入失败 %s: %s", path, e)


def read_versions(store, *, owner: str | None = None, limit: int = 0) -> list[dict[str, Any]]:
    path = versions_index_path(store)
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and (owner is None or data.get("owner") == owner):
                entries.append(data)
    except OSError:
        return []
    if limit and len(entries) > limit:
        entries = entries[-limit:]
    return entries
