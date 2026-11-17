"""公用素材库：入库/匹配/AI 判定解析/复用拷贝/worker 集成（匹配不出图、回存）。

库根目录在测试里全部显式传 tmp（或 monkeypatch library_root），不碰真实用户库。
"""
from __future__ import annotations

import io
import json

import pytest
from PIL import Image

from src.novel_writer.core.drama import DramaStore
from src.novel_writer.core.drama_library import (
    apply_match, asset_view_paths, donate, find_entry, judge_library_donations,
    judge_library_matches, load_catalog, local_match, _complete_views,
)
from src.novel_writer.core.llm.base import LLMResponse


def png(size=(96, 128), color="red") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def write_image(root, name, size=(96, 128), color="red"):
    folder = root / "images"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(png(size, color))
    return path


@pytest.fixture()
def store(tmp_path):
    st = DramaStore(tmp_path)
    st.ensure({"title": "测试"})
    (tmp_path / "meta.json").write_text(
        json.dumps({"title": "测试", "genre": "古装", "style": "快节奏"}, ensure_ascii=False),
        encoding="utf-8")
    return st


@pytest.fixture()
def lib(tmp_path):
    return tmp_path / "userlib"


@pytest.fixture(scope="module", autouse=True)
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _asset(kind, asset_id, name, source="storyboard", **extra):
    return {"kind": kind, "id": asset_id, "name": name, "source": source,
            "prompt": "", "ref": "", "status": "draft", "notes": "",
            "entity": "", "image": "", **extra}


def _character_entry(lib, entry_id="lib_character_passerby", name="路人甲"):
    views = {}
    for view in ("front", "left", "right", "back"):
        path = write_image(lib, f"{entry_id}_{view}.png")
        views[view] = path.relative_to(lib).as_posix()
    for view in ("fb_front", "fb_side", "fb_back"):
        path = write_image(lib, f"{entry_id}_{view}.png", color="green")
        views[view] = path.relative_to(lib).as_posix()
    return {"id": entry_id, "kind": "character", "name": name,
            "desc": "古代街头的普通路人", "prompt": "", "views": views,
            "image_size": "96x128", "source_project": "", "created_at": ""}


# ── 回存（donate）──


def test_donate_complete_views_and_duplicate_skips(store, lib):
    store.save_assets([_asset("character", "char_a", "路人甲")])
    for view in ("front", "left", "right", "back"):
        store.save_makeup_view("char_a", view, png())
    asset = store.load_assets()[0]

    entry_id = donate(store, asset, root=lib)
    assert entry_id == "lib_character_路人甲"
    catalog = load_catalog(lib)
    assert len(catalog) == 1
    entry = catalog[0]
    assert entry["kind"] == "character"
    assert set(entry["views"]) == {"front", "left", "right", "back"}
    for rel in entry["views"].values():
        assert (lib / rel).is_file()

    # 重复入库：同名同类型直接返回已有 id，不新增
    again = donate(store, store.load_assets()[0], root=lib)
    assert again == entry_id
    assert len(load_catalog(lib)) == 1

    # 全身视图成组后补捐会新建独立条目？不——同 id 直接跳过（同名）
    store.save_fullbody_view("char_a", "front", png())
    store.save_fullbody_view("char_a", "side", png((96, 128), "green"))
    store.save_fullbody_view("char_a", "back", png((96, 128), "blue"))
    assert donate(store, store.load_assets()[0], root=lib) == entry_id
    assert len(load_catalog(lib)) == 1


def test_donate_rejects_incomplete_group(store, lib):
    store.save_assets([_asset("prop", "prop_cup", "茶碗")])
    store.save_prop_view("prop_cup", "front", png((64, 64)))
    with pytest.raises(ValueError, match="不成组"):
        donate(store, store.load_assets()[0], root=lib)


def test_donate_scene_group(store, lib):
    store.save_assets([_asset("location", "loc_alley", "小巷")])
    store.save_scene_panorama("loc_alley", png((256, 128)))
    asset = store.load_assets()[0]
    entry_id = donate(store, asset, root=lib)
    entry = find_entry(load_catalog(lib), entry_id)
    assert set(entry["views"]) == {"panorama", "forward", "reverse"}


# ── 复用（apply_match）──


def test_apply_match_character_copies_all_layers(store, lib):
    entry = _character_entry(lib)
    store.save_assets([_asset("character", "passerby", "路人乙")])
    asset = store.load_assets()[0]

    updated = apply_match(store, asset, entry, root=lib)
    assert updated["library_id"] == entry["id"]
    assert updated["library_name"] == "路人甲"
    assert updated["status"] == "ready"
    assert set(updated["makeup_views"]) == {"front", "left", "right", "back"}
    assert set(updated["fullbody_views"]) == {"front", "side", "back"}
    # 库图拷进项目（不引用项目外路径），正面照同时是定妆锚点
    for rel in updated["makeup_views"].values():
        assert rel.startswith("generated/library/")
        assert (store.assets_dir / rel).is_file()
    assert updated["reference_image"] == updated["makeup_views"]["front"]
    assert updated["image"] == updated["makeup_views"]["front"]
    # 与自产资产同口径：审核证据能取到全部视图
    assert len(asset_view_paths(store, store.load_assets()[0])) == 7


def test_apply_match_prop_and_status(store, lib):
    views = {}
    for view in ("front", "side", "back"):
        path = write_image(lib, f"lib_prop_cup_{view}.png", size=(64, 64))
        views[view] = path.relative_to(lib).as_posix()
    entry = {"id": "lib_prop_cup", "kind": "prop", "name": "茶碗", "desc": "",
             "prompt": "", "views": views, "image_size": "", "source_project": "", "created_at": ""}
    store.save_assets([_asset("prop", "prop_wan", "粗碗")])
    updated = apply_match(store, store.load_assets()[0], entry, root=lib)
    assert updated["status"] == "ready"  # 三视图齐 → ready
    assert set(updated["prop_views"]) == {"front", "side", "back"}
    assert updated["reference_image"] == updated["prop_views"]["front"]


def test_apply_match_rejects_kind_mismatch(store, lib):
    entry = _character_entry(lib)
    store.save_assets([_asset("prop", "prop_x", "路人甲")])
    with pytest.raises(ValueError, match="类型不匹配"):
        apply_match(store, store.load_assets()[0], entry, root=lib)


# ── 匹配 ──


def test_local_match_exact_containment_and_kind_filter(lib):
    catalog = [_character_entry(lib), ]
    assert local_match("character", "路人甲（卖菜的）", catalog)["id"].startswith("lib_character")
    assert local_match("character", "路人丙", catalog) is None
    assert local_match("prop", "路人甲", catalog) is None  # 类型不同不跨类匹配


class ScriptedLLM:
    model = "offline"

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def chat(self, messages, **kwargs):
        prompt = messages[-1].content
        self.prompts.append(prompt)
        return LLMResponse(self.responses.pop(0), usage={"total_tokens": 1})


def test_judge_library_matches_parses_and_validates(lib):
    entry = _character_entry(lib)
    catalog = [entry]
    llm = ScriptedLLM([json.dumps({
        "matches": [
            {"asset_id": "a1", "library_id": entry["id"], "reason": "同名路人"},
            {"asset_id": "a2", "library_id": "lib_not_exist", "reason": "瞎编"},
            {"asset_id": "a3", "library_id": entry["id"]},
        ],
        "extra": "忽略",
    })])
    candidates = [
        {"asset_id": "a1", "kind": "character", "name": "路人甲", "source": "storyboard", "desc": ""},
        {"asset_id": "a2", "kind": "character", "name": "路人乙", "source": "storyboard", "desc": ""},
        {"asset_id": "a3", "kind": "character", "name": "路人丙", "source": "storyboard", "desc": ""},
    ]
    import asyncio

    hits = asyncio.run(judge_library_matches(llm, candidates, catalog))
    assert set(hits) == {"a1", "a3"}  # 库里不存在的 id 不采纳
    assert hits["a1"]["reason"] == "同名路人"
    assert hits["a3"]["reason"].startswith("AI 判定可复用")


def test_judge_library_matches_bad_json_returns_empty(lib):
    import asyncio

    llm = ScriptedLLM(["抱歉，我无法输出 JSON。"])
    assert asyncio.run(judge_library_matches(llm, [], [_character_entry(lib)])) == {}


def test_judge_library_donations_filters_ids():
    import asyncio

    llm = ScriptedLLM([json.dumps({"donate": ["prop_ok", "prop_ghost"]})])
    candidates = [{"asset_id": "prop_ok", "kind": "prop", "name": "茶碗",
                   "source": "storyboard", "desc": ""}]
    assert asyncio.run(judge_library_donations(llm, candidates)) == ["prop_ok"]


# ── worker 集成：匹配不出图、回存入库 ──


class LibraryLLM:
    """复用判定 + 审核 + 回存判定一体；按提示词特征路由。"""
    model = "offline-vision"

    def __init__(self, library_id):
        self.library_id = library_id
        self.prompts: list[str] = []

    async def chat(self, messages, **kwargs):
        prompt = messages[-1].content
        self.prompts.append(prompt)
        if "公用素材库」复用判定" in prompt:
            return LLMResponse(json.dumps({"matches": [
                {"asset_id": "passerby", "library_id": self.library_id, "reason": "同名路人"}]}),
                usage={})
        if '"donate"' in prompt and "存入" in prompt:
            return LLMResponse(json.dumps({"donate": ["prop_wan"]}), usage={})
        if "逐条判断：满足" in prompt:
            return LLMResponse("无遗漏", usage={})
        return LLMResponse("通过", usage={})


class NoGenerateClient:
    """路人甲应从库复用（绝不调 generate）；粗碗走正常生成。"""
    model = "offline"

    def __init__(self):
        self.calls: list[str] = []

    def generate(self, positive, negative="", *, prefix, width, height, reference=None, **kwargs):
        self.calls.append(prefix)
        assert (width, height) == (1024, 1024)  # 道具三视图 1:1
        return png((128, 128), "gray")

    def close(self):
        pass


def test_worker_reuses_library_and_donates_generic(store, lib, monkeypatch):
    from src.novel_writer.core import drama_library
    from src.novel_writer.ui.drama.drama_worker import DramaAIWorker

    entry = _character_entry(lib)
    drama_library.save_catalog([entry], lib)  # 目录要落盘，否则库里是空的
    monkeypatch.setattr(drama_library, "library_root", lambda root=None: lib)

    store.save_assets([
        _asset("character", "passerby", "路人甲", source="storyboard"),
        _asset("prop", "prop_wan", "粗碗", source="storyboard"),
    ])
    client = NoGenerateClient()
    monkeypatch.setattr("src.novel_writer.core.image_generation.create_client", lambda cfg: client)
    monkeypatch.setattr("src.novel_writer.core.image_generation.config_from_app",
                        lambda: {"provider": "api", "base_url": "https://offline.invalid", "model": "offline"})
    llm = LibraryLLM(entry["id"])
    approvals_seen: list[str] = []
    worker = DramaAIWorker(llm, store, {"selected_assets": ["passerby", "prop_wan"]},
                           "asset_images", current_episode=1,
                           approval_handler=lambda request:
                           approvals_seen.append(request["title"]) or True)
    ok, errors = [], []
    worker.finished_ok.connect(ok.append)
    worker.failed.connect(errors.append)
    worker.run()
    assert not errors, errors
    assert ok

    # 路人甲：从库复用，绝未出图；三层参考拷进项目
    assert client.calls == ["prop_wan_front", "prop_wan_side", "prop_wan_back"]  # 只有粗碗出了三视图
    passerby = next(a for a in store.load_assets() if a["id"] == "passerby")
    assert passerby["library_id"] == entry["id"]
    assert set(passerby["makeup_views"]) == {"front", "left", "right", "back"}
    assert set(passerby["fullbody_views"]) == {"front", "side", "back"}
    assert passerby["status"] == "ready"
    assert any("复用公用库" in p for p in llm.prompts)
    # 人工闸不省：库图进来也要确认
    assert "确认公用库复用" in approvals_seen
    assert "确认设定图" in approvals_seen  # 粗碗走正常确认

    # 粗碗：正常生成，AI 判定通用后回存库
    prop = next(a for a in store.load_assets() if a["id"] == "prop_wan")
    assert set(prop["prop_views"]) == {"front", "side", "back"}
    catalog = load_catalog(lib)
    assert len(catalog) == 2  # 原有路人条目 + 新捐的粗碗
    assert any(item["kind"] == "prop" and item["name"] == "粗碗" for item in catalog)


def test_library_match_task_reports_when_nothing_matches(store, lib, monkeypatch):
    from src.novel_writer.core import drama_library
    from src.novel_writer.ui.drama.drama_worker import DramaAIWorker

    monkeypatch.setattr(drama_library, "library_root", lambda root=None: lib)
    store.save_assets([_asset("character", "hero", "独有主角", source="canon")])
    llm = LibraryLLM("whatever")
    worker = DramaAIWorker(llm, store, {"selected_assets": ["hero"]},
                           "library_match", current_episode=1,
                           approval_handler=lambda request: True)
    ok, errors = [], []
    worker.finished_ok.connect(ok.append)
    worker.failed.connect(errors.append)
    worker.run()
    assert not errors, errors
    # 正典资产不参与匹配 → 直接无可匹配
    assert any("没有可匹配" in line for line in ok)


def test_assets_page_donate_button_flow(qapp, store, lib, monkeypatch):
    """资产页「存入公用库」：成组资产入库，弹窗汇总（不碰真实用户库）。"""
    from src.novel_writer.core import drama_library
    from src.novel_writer.ui.drama.drama_workspace import _AssetsPage

    monkeypatch.setattr(drama_library, "library_root", lambda root=None: lib)
    store.save_assets([_asset("character", "char_a", "路人甲"),
                       _asset("prop", "prop_cup", "茶碗")])
    for view in ("front", "left", "right", "back"):
        store.save_makeup_view("char_a", view, png())
    store.save_prop_view("prop_cup", "front", png((64, 64)))  # 不成组 → 入库失败

    page = _AssetsPage()
    page._store = store
    page.load(store)
    page.table.selectAll()
    boxes = []
    monkeypatch.setattr("src.novel_writer.ui.drama.drama_workspace.QMessageBox.information",
                        lambda *_a, **_k: boxes.append(_a[1]))
    page._donate_library()
    assert boxes and boxes[0] == "存入公用库"
    catalog = load_catalog(lib)
    assert [item["name"] for item in catalog] == ["路人甲"]
