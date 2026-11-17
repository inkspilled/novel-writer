"""制作链共用的版本、审核证据、确认快照。"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import shutil
import uuid
import re
from datetime import datetime
from pathlib import Path

from . import project_io
from .drama_scene import scene_view_for_shot


def preserve(path: Path) -> None:
    if path.is_file():
        folder = path.parent / "versions"
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, folder / f"{path.stem}_{stamp()}{path.suffix}")


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid.uuid4().hex[:6]


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    default=str).encode("utf-8")).hexdigest()


def file_digest(path: Path) -> str:
    if not path.is_file():
        return "missing"
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot(store, episode: int) -> str:
    """排除进度和视频输出；每生成一镜不能使下一镜的审核失效。"""
    series = store.load_series()
    shots = store.load_storyboard(episode)["shots"]
    assets = store.load_assets()
    files = {}
    for shot in shots:
        for which in ("first", "last"):
            path = store.keyframe_image_path(episode, shot["shot_id"], which)
            files[str(path)] = file_digest(path)
    for asset in assets:
        for path in (*store.makeup_paths(asset).values(), *store.scene_paths(asset).values()):
            files[str(path)] = file_digest(path)
        ref = asset.get("reference_image") or asset.get("image")
        if ref:
            path = asset_path(store, ref)
            files[str(path)] = file_digest(path)
    return digest({
        "video_config": video_config_snapshot(),
        "series": {k: series.get(k) for k in (
            "style", "palette", "palette_table", "aspect", "requirements", "structure",
            "music", "music_file", "subtitles", "logline",
        )},
        "story": store.load_script(episode), "bible": store.bible_text(), "shots": shots,
        "briefs": store.load_keyframes(episode)["shots"], "assets": assets, "files": files,
    })


def video_config_snapshot():
    from .video_generation import config_from_app
    cfg = config_from_app()
    return {"provider":cfg.get("provider", "comfyui"), "model":cfg.get("model") or "",
            "resolution":cfg.get("resolution") or "720p", "fps":cfg.get("fps") or 16,
            "prompt_suffix":cfg.get("prompt_suffix") or "", "extra_parameters":cfg.get("extra_parameters") or {}}


def asset_path(store, ref) -> Path:
    path = Path(ref)
    if path.is_absolute():
        return path
    candidate = store.assets_dir / path
    return candidate if candidate.is_file() else store.root / path


def confirmation_key(store, episode: int, kind: str) -> str:
    series = store.load_series()
    if kind == "story":
        return digest(store.load_script(episode))
    return digest({k: series.get(k) for k in ("style", "palette", "palette_table", "aspect")})


def confirmed(store, episode: int, kind: str) -> bool:
    data = read_state(store.root / "review" / "confirmations.json")
    return data.get(f"{kind}:{episode}") == confirmation_key(store, episode, kind)


def confirm(store, episode: int, kind: str) -> None:
    path = store.root / "review" / "confirmations.json"
    data = read_state(path)
    data[f"{kind}:{episode}"] = confirmation_key(store, episode, kind)
    project_io.write_json(path, data)


def read_state(path: Path) -> dict:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"状态文件无效：{path}")
    return value


def image_urls(paths: list[Path]) -> list[str]:
    from PIL import Image
    urls = []
    for path in paths:
        with Image.open(path) as im:
            buffer = io.BytesIO()
            im.convert("RGB").save(buffer, format="JPEG", quality=95)
        urls.append("data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"))
    return urls


def frame_evidence(store, episode: int, shot: dict) -> list[Path]:
    """全分辨率首尾帧 + 半透明叠图 + 手机缩略；不是仅把文件名交给模型。"""
    from PIL import Image
    sid = shot["shot_id"]
    first = store.keyframe_image_path(episode, sid, "first")
    last = store.keyframe_image_path(episode, sid, "last")
    folder = store.exports_dir / "evidence" / f"ep{episode:03d}" / sid
    folder.mkdir(parents=True, exist_ok=True)
    with Image.open(first) as a, Image.open(last) as b:
        if a.size != b.size:
            raise ValueError(f"{sid} 首尾帧尺寸不一致，请先裁切")
        ratio = store.load_series().get("aspect", "9:16")
        w, h = map(int, ratio.split(":"))
        if abs(a.width / a.height / (w / h) - 1) > .02:
            raise ValueError(f"{sid} 首尾帧画幅不符 {ratio}，请先裁切")
        overlay = folder / "overlay.png"
        Image.blend(a.convert("RGB"), b.convert("RGB"), .5).save(overlay)
        mobile = a.convert("RGB")
        mobile.thumbnail((360, 640))
        phone = folder / "mobile.png"
        mobile.save(phone)
    refs = []
    assets = store.load_assets()
    assets.sort(key=lambda asset: not (asset["kind"] == "location" and asset["name"] == shot.get("location")))
    for asset in assets:
        if len(refs) >= 4:
            break
        if asset.get("name") and asset["name"] in str(shot):
            scene = store.scene_paths(asset) if asset["name"] == shot.get("location") else {}
            scene_ref = scene.get(scene_view_for_shot(str(shot)))
            if scene_ref:
                refs.append(scene_ref)
                continue
            views = store.makeup_paths(asset)
            if len(views) == 4:
                sheet = Image.new("RGB", (960, 320), "white")
                for index, path in enumerate(views.values()):
                    with Image.open(path) as image:
                        picture = image.convert("RGB")
                        picture.thumbnail((240, 320))
                        sheet.paste(picture, (index * 240 + (240 - picture.width) // 2, 0))
                path = folder / f"makeup_{len(refs)}.jpg"
                sheet.save(path, quality=90)
                refs.append(path)
                continue
            ref = asset.get("reference_image") or asset.get("image")
            if ref and asset_path(store, ref).is_file():
                refs.append(asset_path(store, ref))
    return [first, last, overlay, phone, *refs]


def crop_push(data: bytes, zoom: float = 1.12) -> bytes:
    from PIL import Image
    with Image.open(io.BytesIO(data)) as image:
        w, h = image.size
        cw, ch = int(w / zoom), int(h / zoom)
        crop = image.crop(((w-cw)//2, (h-ch)//2, (w+cw)//2, (h+ch)//2))
        out = io.BytesIO()
        crop.resize((w, h), Image.Resampling.LANCZOS).save(out, format="PNG")
        return out.getvalue()


def review_frame_time(path: Path, fps=24) -> float:
    match = re.search(r"time_([\d.]+)s", path.name)
    if match:
        return float(match.group(1))
    match = re.search(r"cut_(\d+)_(\d+)", path.name)
    if match:
        return int(match.group(1))/1000 + (int(match.group(2))-1-3)/fps
    return 0.0
