"""地点身份、坐标与相似候选。空间坐标不等于语义向量，候选不会自动合并。"""
from __future__ import annotations

import math
import unicodedata
import uuid
from collections import Counter


def location_key(name: str) -> str:
    return "".join(unicodedata.normalize("NFKC", str(name)).split()).casefold()


def resolve_location(locations: dict, name: str, parent: str = "") -> str:
    if name in locations:
        return name
    matches = []
    for canonical, info in locations.items():
        if not isinstance(info, dict):
            continue
        if parent and info.get("parent") and location_key(parent) != location_key(info["parent"]):
            continue
        if any(location_key(name) == location_key(label) for label in [canonical, *info.get("aliases", [])]):
            matches.append(canonical)
    return matches[0] if len(matches) == 1 else name.strip()


def validate_geometry(info: dict) -> dict:
    result = dict(info)
    coords = result.get("coordinates")
    if coords is not None:
        if not isinstance(coords, (list, tuple)) or len(coords) not in (2, 3):
            raise ValueError("坐标必须是二维或三维数值，例如 10,20 或 10,20,0")
        if any(isinstance(v, bool) for v in coords):
            raise ValueError("坐标必须为有限数值")
        try:
            coords = [float(v) for v in coords]
        except (TypeError, ValueError):
            raise ValueError("坐标必须为有限数值") from None
        if not all(math.isfinite(v) for v in coords):
            raise ValueError("坐标必须为有限数值")
        if not result.get("coordinate_frame") or not result.get("coordinate_unit"):
            raise ValueError("填写坐标时必须指定坐标系和单位，同一坐标系应使用统一单位")
        result["coordinates"] = coords
    return result


def location_distance(first: dict, second: dict) -> float | None:
    a, b = first.get("coordinates"), second.get("coordinates")
    if (a is None or b is None or len(a) != len(b)
            or not first.get("coordinate_frame")
            or first.get("coordinate_frame") != second.get("coordinate_frame")
            or first.get("coordinate_unit") != second.get("coordinate_unit")):
        return None
    return math.dist(a, b)


def validate_spatial_record(locations: dict, name: str, info: dict) -> dict:
    result = validate_geometry(info)
    if result.get("coordinates") is not None:
        for other, existing in locations.items():
            if other == name or not isinstance(existing, dict) or existing.get("coordinates") is None:
                continue
            if existing.get("coordinate_frame") == result["coordinate_frame"]:
                if existing.get("coordinate_unit") != result["coordinate_unit"] or len(existing["coordinates"]) != len(result["coordinates"]):
                    raise ValueError("同一坐标系必须统一坐标单位与维数；其他单位请先换算，其他空间请另建坐标系")
    return result


def ensure_identity(name: str, info: dict) -> dict:
    result = dict(info)
    result.setdefault("location_id", "loc_" + uuid.uuid5(uuid.NAMESPACE_URL, location_key(name)).hex[:12])
    aliases = result.get("aliases", [])
    if isinstance(aliases, str):
        aliases = [x.strip() for x in aliases.replace("，", "、").split("、") if x.strip()]
    result["aliases"] = list(dict.fromkeys(str(x).strip() for x in aliases if str(x).strip() and str(x).strip() != name))
    return result


def _text_vector(text: str) -> Counter:
    text = location_key(text)
    return Counter(text[i:i + 2] for i in range(max(1, len(text) - 1)))


def duplicate_candidates(locations: dict, name: str) -> list[tuple[str, float]]:
    """使用本地字符二元组向量余弦找疑似重复；父区域冲突的地点不参与。"""
    current = locations.get(name) or {}
    a = _text_vector(name + str(current.get("description") or ""))
    results = []
    for other, info in locations.items():
        if other == name or not isinstance(info, dict):
            continue
        if current.get("parent") and info.get("parent") and current["parent"] != info["parent"]:
            continue
        b = _text_vector(other + str(info.get("description") or ""))
        denom = math.sqrt(sum(v*v for v in a.values()) * sum(v*v for v in b.values()))
        score = sum(v*b[k] for k, v in a.items()) / denom if denom else 0
        if score >= 0.3:
            results.append((other, score))
    return sorted(results, key=lambda pair: pair[1], reverse=True)[:8]
