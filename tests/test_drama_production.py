"""制作链集成回归：审批、真实图证据、审核失效、任务续跑。所有服务离线。"""
import asyncio
import io
import json

import httpx
import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from src.novel_writer.core.drama import DramaStore
from src.novel_writer.core.drama_production import confirm, frame_evidence
from src.novel_writer.core.drama_review import parse_verdict, save_review, submit_gate
from src.novel_writer.core.llm.base import LLMMessage, LLMResponse
from src.novel_writer.core.seedance import SeedanceClient, SeedanceError, build_payload, generation_seconds
from src.novel_writer.ui.drama.drama_worker import DramaAIWorker


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


def png(color="red", size=(72,128)):
    stream = io.BytesIO()
    Image.new("RGB", size, color).save(stream, "PNG")
    return stream.getvalue()


@pytest.fixture
def store(tmp_path):
    s = DramaStore(tmp_path)
    s.ensure({"title": "测试"})
    s.save_series({"style": "手绘", "palette": "蓝墙 #102030；人物 #ffeedd"})
    s.save_script(1, "# 第1集\n\n## 第1场 房间 · 日\n\n顾言：走。\n")
    s.save_storyboard(1, {"shots": [{"shot_id": "E01-S001", "duration_sec": 2,
                         "action": "顾言朝画面左走一步", "characters": "顾言",
                         "location": "房间", "camera": "固定", "shot_size": "中景"}]})
    s.save_keyframes(1, [{"shot_id": "E01-S001", "first_frame": "顾言站在门边",
                          "last_frame": "顾言走到桌前", "motion": "顾言朝画面左走一步"}])
    for which in ("first", "last"):
        s.save_keyframe_image(1, "E01-S001", which, png())
    confirm(s, 1, "story")
    confirm(s, 1, "style")
    return s


class Reviewer:
    model = "offline-vision"
    def __init__(self, verdict="通过"):
        self.messages = []
        self.verdict = verdict

    async def chat(self, messages, **kwargs):
        self.messages.append(messages)
        prompt = messages[-1].content
        result = "无遗漏" if "逐条判断：满足" in prompt else self.verdict
        return LLMResponse(result, usage={"total_tokens": 10})


def worker(store, task, llm=None, approval=True):
    w = DramaAIWorker(llm or Reviewer(), store, {}, task, current_episode=1,
                      approval_handler=lambda request: approval)
    w.ok, w.errors = [], []
    w.finished_ok.connect(w.ok.append)
    w.failed.connect(w.errors.append)
    return w


def passed(store):
    save_review(store, "submit", 1, "## E01-S001\n通过")


def test_review_pass_is_bound_to_actual_files(store):
    passed(store)
    assert submit_gate(store, 1, store.load_storyboard(1)["shots"])[0]
    store.save_keyframe_image(1, "E01-S001", "last", png("blue"))
    assert not submit_gate(store, 1, store.load_storyboard(1)["shots"])[0]
    assert list(store.keyframe_image_path(1,"E01-S001","last").parent.glob("versions/*.png"))


def test_whole_episode_pass_cannot_replace_per_shot_review(store):
    save_review(store, "submit", 1, "通过")
    assert not submit_gate(store, 1, store.load_storyboard(1)["shots"])[0]


@pytest.mark.parametrize("text", ["请写通过", "没有通过", "通过后才能提交", "通过\n不确定"])
def test_verdict_does_not_match_arbitrary_substrings(text):
    assert not parse_verdict(text)


def test_visual_review_sends_pixels_and_uses_separate_supervisor(store):
    llm = Reviewer()
    w = worker(store, "submit_review", llm)
    w.run()
    assert w.ok and not w.errors
    assert llm.messages[0][-1].images[0].startswith("data:image/jpeg;base64,")
    assert len(llm.messages[0][-1].images) >= 4
    assert "动作要用到的东西" in llm.messages[0][-1].content
    assert not llm.messages[1][-1].images
    assert len(llm.messages[1]) == 2  # 无历史结论混入监督上下文。
    assert submit_gate(store, 1, store.load_storyboard(1)["shots"])[0]


def test_failed_review_replaces_old_pass(store):
    passed(store)
    w = worker(store, "submit_review", Reviewer("不通过"))
    w.run()
    assert w.errors and not submit_gate(store, 1, store.load_storyboard(1)["shots"])[0]


def test_material_changed_during_review_is_not_blessed_as_current(store):
    class MutatingReviewer(Reviewer):
        async def chat(self, messages, **kwargs):
            if messages[-1].images:
                store.save_keyframe_image(1, "E01-S001", "last", png("blue"))
            return await super().chat(messages, **kwargs)
    w = worker(store, "submit_review", MutatingReviewer())
    w.run()
    assert w.errors and "审核期间" in w.errors[0]
    assert not submit_gate(store, 1, store.load_storyboard(1)["shots"])[0]


def test_video_spec_change_invalidates_old_review(store, monkeypatch):
    from src.novel_writer.core import video_generation
    passed(store)
    monkeypatch.setattr(video_generation, "config_from_app", lambda: {
        "provider":"seedance", "model":"doubao-seedance-2-0-mini-260615", "fps":24})
    assert not submit_gate(store, 1, store.load_storyboard(1)["shots"])[0]


def test_global_rejection_cannot_be_overridden_by_per_shot_pass(store):
    save_review(store, "submit", 1, "## E01-S001\n通过", verdict="不通过")
    assert not submit_gate(store, 1, store.load_storyboard(1)["shots"])[0]


def test_project_task_lock_rejects_concurrent_generation(store):
    from src.novel_writer.core.seedance_tasks import SeedanceTasks

    with SeedanceTasks(store.root).locked():
        with pytest.raises(RuntimeError, match="正在运行"):
            with SeedanceTasks(store.root).locked():
                pytest.fail("同项目重复取得任务锁")


def test_rejected_asset_is_reviewed_on_resume_without_regenerating(store, monkeypatch):
    from src.novel_writer.core import image_generation
    store.save_script(1, "# 第1集\n\n## 第1场 房间 · 日\n\n顾言：先等等。\n")
    store.save_series({"palette": ""})
    store.save_assets([{"id":"hero", "name":"顾言", "kind":"character", "prompt":"单人正面立绘"}])
    calls = []
    class Client:
        def generate(self, positive, negative, *, prefix, width, height, reference=None):
            calls.append((prefix, reference))
            return png(size=(960,1280))
        def close(self):
            pass
    class RejectAsset(Reviewer):
        async def chat(self, messages, **kwargs):
            if "这一步是：设定图视觉审核" in messages[-1].content:
                assert len(messages[-1].images) == 7  # 4 定妆 + 3 全身
                return LLMResponse("不通过")
            return await super().chat(messages, **kwargs)
    monkeypatch.setattr(image_generation, "config_from_app", lambda: {"provider":"api", "base_url":"https://offline.invalid", "model":"offline"})
    monkeypatch.setattr(image_generation, "create_client", lambda cfg: Client())
    first = worker(store, "asset_images", RejectAsset())
    first.run()
    assert first.errors
    views = store.makeup_paths(store.load_assets()[0])
    assert list(views) == ["front", "left", "right", "back"]
    fb = store.fullbody_paths(store.load_assets()[0])
    expected = [("hero_front", None),
                *((f"hero_{angle}", views["front"]) for angle in ("left", "right", "back")),
                ("hero_fb_front", views["front"]),
                *((f"hero_fb_{view}", fb["front"]) for view in ("side", "back"))]
    assert calls == expected
    second = worker(store, "asset_images")
    second.run()
    assert second.ok and not second.errors
    assert calls == expected
    assert len(second.llm.messages) >= 4  # 确实重审，而不是因已有图片直接跳过。
    assert not (store.root / "review" / "story_ep001.md").exists()


def test_rejected_prompt_can_be_rewritten_without_generating_images(store, monkeypatch):
    from src.novel_writer.core import image_generation

    store.save_assets([{"id": "hero", "name": "顾言", "kind": "character", "prompt": "相互矛盾的描述"},
                        {"id": "other", "name": "其他人物", "kind": "character", "prompt": "保留"}])
    monkeypatch.setattr(image_generation, "config_from_app", lambda: {
        "provider": "api", "base_url": "https://offline.invalid", "model": "offline",
    })
    monkeypatch.setattr(image_generation, "create_client", lambda cfg: type("Client", (), {
        "generate": lambda *args, **kwargs: pytest.fail("审核失败不能出图"),
        "close": lambda self: None,
    })())
    failed = worker(store, "asset_images", Reviewer("衣领描述有冲突\n不通过"))
    failed.project_info["selected_assets"] = ["hero"]
    failed.run()
    assert failed.errors and failed.asset_prompt_failure["asset_id"] == "hero"
    assert "衣领描述有冲突" in failed.asset_prompt_failure["review"]
    report = store.root / "review" / "asset_prompt_review.json"
    assert json.loads(report.read_text(encoding="utf-8")) == failed.asset_prompt_failure
    rewritten = worker(store, "asset_prompt_rewrite", Reviewer('{"prompt":"顾言，青色交领长袍"}'))
    rewritten.project_info["prompt_rewrite"] = failed.asset_prompt_failure
    rewritten.run()
    assert rewritten.ok and not rewritten.errors
    assert [asset["prompt"] for asset in store.load_assets()] == ["顾言，青色交领长袍", "保留"]


def test_frame_generation_stops_at_failed_story_review(store):
    store.save_script(1, "# 第1集\n\n## 第1场 房间 · 日\n\n顾言：先等等。\n")
    store.save_storyboard(1, store.load_storyboard(1), update_source=True)
    w = worker(store, "frame_images", Reviewer("不通过"))
    w.run()
    report = store.root / "review" / "story_ep001.md"
    assert report.is_file()
    assert not w.ok and str(report) in w.errors[0]
    assert len(w.llm.messages) == 1


def test_character_makeup_resumes_only_missing_views(store, monkeypatch):
    from src.novel_writer.core import image_generation

    store.save_assets([{"id": "hero", "name": "顾言", "kind": "character"}])
    front = store.save_makeup_view("hero", "front", png(size=(96, 128)))
    left = store.save_makeup_view("hero", "left", png(size=(96, 128)))
    calls = []

    class Client:
        def generate(self, positive, negative, *, prefix, width, height, reference=None):
            with Image.open(reference) as image:
                assert image.size == (960, 1280)
            calls.append(prefix)
            return png(size=(width, height))

        def close(self):
            pass

    monkeypatch.setattr(image_generation, "config_from_app",
                        lambda: {"provider": "api", "base_url": "https://offline.invalid", "model": "offline"})
    monkeypatch.setattr(image_generation, "create_client", lambda cfg: Client())
    w = worker(store, "asset_images")
    w.run()
    assert w.ok and not w.errors
    # 缺的定妆角度 + 全部全身三视图（第一层参考齐了才排到全身）
    assert calls == ["hero_right", "hero_back", "hero_fb_front", "hero_fb_side", "hero_fb_back"]
    views = store.makeup_paths(store.load_assets()[0])
    assert views["front"] == front and views["left"] == left
    store.clear_makeup_view("hero", "left")
    w = worker(store, "asset_images")
    w.run()
    assert w.ok and not w.errors
    assert calls[-1] == "hero_left" and len(calls) == 6
    assert left.is_file()


def test_scene_panorama_generates_opposing_views_and_reuses_them(store, monkeypatch):
    from PIL import ImageDraw
    from src.novel_writer.core import image_generation

    store.save_assets([{"id": "hall", "name": "房间", "kind": "location",
                        "notes": "主入口对面两根红柱，左侧楼梯"}])
    calls = []

    class Client:
        def generate(self, positive, negative, *, prefix, width, height, reference=None):
            calls.append(prefix)
            assert (width, height) == (2048, 1024)
            assert "等距柱状投影" in positive and "两根红柱" in positive
            assert "16:9" not in positive
            image = Image.new("RGB", (width, height), "blue")
            ImageDraw.Draw(image).rectangle((width // 4, 0, width * 3 // 4, height), fill="red")
            buffer = io.BytesIO()
            image.save(buffer, "PNG")
            return buffer.getvalue()

        def close(self):
            pass

    monkeypatch.setattr(image_generation, "config_from_app",
                        lambda: {"provider": "api", "base_url": "https://offline.invalid", "model": "offline"})
    monkeypatch.setattr(image_generation, "create_client", lambda cfg: Client())
    first = worker(store, "asset_images")
    first.run()
    assert first.ok and not first.errors
    paths = store.scene_paths(store.load_assets()[0])
    assert list(paths) == ["panorama", "forward", "reverse"]
    assert any(len(messages[-1].images) == 3 for messages in first.llm.messages)
    for view, color in (("forward", (255, 0, 0)), ("reverse", (0, 0, 255))):
        with Image.open(paths[view]) as image:
            assert image.size == (1280, 720)
            assert image.getpixel((640, 360)) == color
    shot = {**store.load_storyboard(1)["shots"][0], "camera": "平视反打"}
    assert paths["reverse"] in frame_evidence(store, 1, shot)
    paths["reverse"].unlink()
    second = worker(store, "asset_images")
    second.run()
    assert second.ok and not second.errors
    assert paths["reverse"].is_file()
    assert calls == ["hall_panorama"]


def test_makeup_prompt_does_not_require_face_details_from_behind(store):
    asset = {"kind": "character", "name": "顾言"}
    back, _ = store.makeup_prompt(asset, "back")
    side, _ = store.makeup_prompt(asset, "left")
    assert "后颈" in back and "面部不可见" in back
    assert all(term not in back for term in ("表情", "五官", "妆容"))
    assert "远侧自然遮挡" in side


def test_manually_modified_frames_are_preserved_on_resume(store, monkeypatch):
    from src.novel_writer.core import image_generation
    calls = []
    class Client:
        def generate(self, *args, **kwargs):
            calls.append(True)
            return png(size=(720,1280))
        def close(self):
            pass
    monkeypatch.setattr(image_generation, "config_from_app", lambda: {"provider":"api", "base_url":"https://offline.invalid", "model":"offline"})
    monkeypatch.setattr(image_generation, "create_client", lambda cfg: Client())
    w = worker(store, "frame_images")
    w.run()
    assert w.ok and not w.errors
    changed = png("blue", (720,1280))
    store.save_keyframe_image(1, "E01-S001", "first", changed)
    again = worker(store, "frame_images")
    again.run()
    assert again.ok and not again.errors
    assert len(calls) == 2
    assert store.keyframe_image_path(1,"E01-S001","first").read_bytes() == changed


def test_selected_storyboard_episode_is_the_execution_target():
    from types import SimpleNamespace
    from src.novel_writer.ui.app_shell.project_mixin import ProjectMixin
    fake = SimpleNamespace(drama_workspace=SimpleNamespace(
        nav=SimpleNamespace(currentRow=lambda:2), storyboard_page=SimpleNamespace(_current=3),
        script_page=SimpleNamespace(_current=1)))
    assert ProjectMixin._current_drama_episode(fake) == 3


def test_final_review_frames_sort_in_movie_order():
    from pathlib import Path
    from src.novel_writer.core.drama_production import review_frame_time
    files = [Path("time_2.000s_frame_000048.png"), Path("cut_001000_01.png"), Path("time_0.500s_frame_000012.png")]
    assert sorted(files, key=review_frame_time) == [files[2], files[1], files[0]]


def test_frame_evidence_rejects_different_sizes(store):
    store.save_keyframe_image(1, "E01-S001", "last", png(size=(80,128)))
    with pytest.raises(ValueError, match="尺寸"):
        frame_evidence(store, 1, store.load_storyboard(1)["shots"][0])


def test_no_approval_handler_fails_closed(store):
    w = DramaAIWorker(None, store, {}, "video")
    with pytest.raises(RuntimeError, match="批准处理器"):
        asyncio.run(w._approval("确认故事", "请检查剧本"))


def test_connected_approval_signal_can_resume(store):
    w = DramaAIWorker(None, store, {}, "video")
    w.approval_requested.connect(lambda request: w.answer_approval(request["id"], True))
    asyncio.run(w._approval("确认故事", "请检查剧本"))


def test_video_does_not_create_client_before_review(store, monkeypatch):
    from src.novel_writer.core import video_generation
    def forbidden(*args, **kwargs):
        pytest.fail("审核前不能创建生成客户端")
    monkeypatch.setattr(video_generation, "create_client", forbidden)
    w = worker(store, "video")
    w.run()
    assert w.errors and "审核" in w.errors[0]


def test_video_resume_skips_existing_clip(store, monkeypatch):
    from src.novel_writer.core import video_generation as mod
    passed(store)
    calls = []
    class Client:
        def generate(self, *args, **kwargs):
            calls.append(kwargs)
            assert "【逐秒】" in args[0]
            return b"mp4", "clip.mp4"
        def close(self):
            pass
    monkeypatch.setattr(mod, "config_from_app", lambda: {"provider":"comfyui", "workflow":"x"})
    monkeypatch.setattr(mod, "create_client", lambda *args, **kwargs: Client())
    monkeypatch.setattr(mod, "concat_clips", lambda *args: True)
    for _ in range(2):
        w = worker(store, "video")
        w.approval_handler = lambda _: pytest.fail("视频审核通过后直接提交")
        w.run()
        assert w.ok and not w.errors
    assert len(calls) == 1


def seedance(store, handler):
    return SeedanceClient(
        {"api_key": "fake-secret", "model": "doubao-seedance-2-0-mini-260615"},
        drama_dir=store.root, http=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_seedance_payload_uses_top_level_parameters():
    payload = build_payload("test", duration=3, resolution="720p")
    assert payload["duration"] == 3 and payload["resolution"] == "720p"
    assert "parameters" not in payload


def test_cloud_short_shots_use_actual_minimum_duration():
    assert generation_seconds(1.5, "doubao-seedance-2-0-mini-260615") == 4
    with pytest.raises(ValueError, match="frames"):
        build_payload("test", extra_parameters={"frames":1000})


def test_changing_one_shot_does_not_regenerate_others(store, monkeypatch):
    from src.novel_writer.core import video_generation as mod
    shots = store.load_storyboard(1)["shots"]
    shots.append({**shots[0], "shot_id":"E01-S002"})
    store.save_storyboard(1, {"shots":shots})
    briefs = store.load_keyframes(1)["shots"]
    store.save_keyframes(1, [*briefs, {**briefs[0], "shot_id":"E01-S002"}])
    for which in ("first", "last"):
        store.save_keyframe_image(1, "E01-S002", which, png())
    calls = []
    class Client:
        def generate(self, *args, **kwargs):
            calls.append(kwargs["prefix"])
            return b"mp4", "clip.mp4"
        def close(self):
            pass
    monkeypatch.setattr(mod, "config_from_app", lambda: {"provider":"comfyui", "workflow":"x"})
    monkeypatch.setattr(mod, "create_client", lambda *args, **kwargs: Client())
    monkeypatch.setattr(mod, "concat_clips", lambda *args: True)
    for color in ("red", "blue"):
        store.save_keyframe_image(1, "E01-S001", "last", png(color))
        save_review(store, "submit", 1, "## E01-S001\n通过\n\n## E01-S002\n通过")
        w = worker(store, "video")
        w.run()
        assert w.ok and not w.errors
    assert calls == ["E01-S001", "E01-S002", "E01-S001"]


def test_cloud_success_and_resume_do_not_submit_twice(store):
    requests = []
    def handler(req):
        requests.append(req)
        if req.method == "POST":
            payload = json.loads(req.content)
            assert payload["duration"] == 4
            return httpx.Response(200, json={"id":"task-1"})
        if "tasks" in str(req.url):
            return httpx.Response(200, json={"status":"succeeded", "content":{"video_url":"https://files.invalid/clip"}})
        assert "Authorization" not in req.headers
        return httpx.Response(200, content=b"\x00\x00\x00\x18ftypisom")
    client = seedance(store, handler)
    for _ in range(2):
        client.generate("test", length=3.2, shot_id="E01-S001", episode=1)
    assert sum(req.method == "POST" for req in requests) == 1
    records = json.loads(client.tasks.path.read_text(encoding="utf-8"))
    assert records["1:E01-S001"]["state"] == "completed"


def test_ambiguous_submission_blocks_duplicate(store):
    calls = []
    def handler(req):
        calls.append(req)
        raise httpx.ReadTimeout("timeout", request=req)
    client = seedance(store, handler)
    with pytest.raises(RuntimeError):
        client.generate("test", length=4, shot_id="S1", episode=1)
    with pytest.raises(SeedanceError, match="结果不明"):
        client.generate("test", length=4, shot_id="S1", episode=1)
    assert len(calls) == 1


def test_seedance_rejected_request_can_be_retried(store):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(402, text="request rejected")

    client = seedance(store, handler)
    for _ in range(2):
        with pytest.raises(SeedanceError, match="HTTP 402"):
            client.generate("test", length=4, shot_id="S1", episode=1)
    assert len(calls) == 2


def test_legacy_seedance_tasks_are_resumed(store):
    def handler(req):
        assert req.method == "GET"
        if "tasks" in str(req.url):
            return httpx.Response(200, json={"status": "succeeded", "video_url": "https://files.invalid/clip"})
        return httpx.Response(200, content=b"\x00\x00\x00\x18ftypisom")

    client = seedance(store, handler)
    old = {"episode": 1, "shot_id": "S1", "state": "submitted", "task_id": "old-task",
           "input_hash": client.fingerprint("test", None, None, 4), "amount": 2}
    (store.root / "budget.json").write_text(json.dumps({"entries": [old]}), encoding="utf-8")
    client.generate("test", length=4, shot_id="S1", episode=1)
    records = json.loads(client.tasks.path.read_text(encoding="utf-8"))
    assert set(records["1:S1"]) == {"task_id", "state", "input_hash"}


def test_patch_preserves_pixels_outside_box(tmp_path):
    from src.novel_writer.ui.drama.frame_tools import paste_patch
    original, edited = tmp_path / "a.png", tmp_path / "b.png"
    original.write_bytes(png("red", (10,10)))
    edited.write_bytes(png("blue", (10,10)))
    result = Image.open(io.BytesIO(paste_patch(original, edited, (2,2,5,5))))
    assert result.getpixel((0,0)) == (255,0,0)
    assert result.getpixel((3,3)) == (0,0,255)


def test_all_llm_drivers_serialize_actual_images():
    from src.novel_writer.core.llm.client import _serialize_messages
    from src.novel_writer.core.llm.claude import _content
    from src.novel_writer.core.llm.ollama import OllamaLLM
    m = LLMMessage("user", "look", images=["data:image/png;base64,AAAA"])
    assert _serialize_messages([m])[0]["content"][1]["image_url"]["url"] == m.images[0]
    assert _content(m)[1]["source"]["data"] == "AAAA"
    fake = object.__new__(OllamaLLM)
    fake.model = "offline"
    assert fake._build_payload([m], .2, 100, False, "off")["messages"][0]["images"] == ["AAAA"]


@pytest.mark.parametrize("options", [[], {"fps":0}, {"repair":{"drop_first":True}}, {"grade":"yes"}])
def test_post_options_reject_invalid_or_untargeted_repairs(options):
    from src.novel_writer.core.drama_post import validate_options
    with pytest.raises(ValueError):
        validate_options(options)


def test_seedance_settings_can_be_saved_without_comfy_workflow(qapp):
    from src.novel_writer.ui.settings.video_dialog import VideoDialog
    d = VideoDialog({"video_generation": {"price_per_second": .5}})
    d.provider.setCurrentIndex(d.provider.findData("seedance"))
    d._save()
    assert d.get_config()["video_generation"]["provider"] == "seedance"
    assert "price_per_second" not in d.get_config()["video_generation"]


def test_approval_window_has_inspectable_outputs(store):
    from src.novel_writer.ui.drama.approval_dialog import ProductionApprovalDialog
    d = ProductionApprovalDialog({"title":"故事", "text":"确认", "paths":[str(store.script_path(1))]})
    from PySide6.QtWidgets import QPlainTextEdit
    assert "顾言" in d.findChild(QPlainTextEdit).toPlainText()


def test_full_production_runs_through_all_gates_offline(store, monkeypatch):
    import shutil
    import subprocess
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("完整制作链测试需要 ffmpeg/ffprobe")
    from src.novel_writer.core import image_generation, video_generation, drama_preflight
    source = store.project_dir / "source.mp4"
    subprocess.run([shutil.which("ffmpeg"), "-y", "-f", "lavfi", "-i", "color=c=red:s=72x128:r=24",
                    "-t", "3", str(source)], capture_output=True, check=True)
    store.save_series({"post_options":{"upscale":False}})
    calls, approvals = [], []
    class ImageClient:
        def generate(self, positive, negative="", prefix="", width=72, height=128, reference=None):
            calls.append(("image", prefix, bool(reference)))
            return png(size=(width,height))
        def close(self):
            pass
    class VideoClient:
        def generate(self, prompt, first, last, **kwargs):
            calls.append(("video", kwargs["prefix"]))
            assert submit_gate(store, 1, store.load_storyboard(1)["shots"])[0]
            return source.read_bytes(), "clip.mp4"
        def close(self):
            pass
    monkeypatch.setattr(drama_preflight, "write_preflight", lambda store: {"missing":[], "summary":"ok"})
    monkeypatch.setattr(image_generation, "config_from_app", lambda: {"provider":"api", "base_url":"https://offline.invalid", "model":"offline"})
    monkeypatch.setattr(image_generation, "create_client", lambda cfg: ImageClient())
    monkeypatch.setattr(video_generation, "config_from_app", lambda: {"provider":"comfyui", "workflow":"offline"})
    monkeypatch.setattr(video_generation, "create_client", lambda *args, **kwargs: VideoClient())
    w = worker(store, "production")
    w.approval_handler = lambda request: approvals.append(request["title"]) or True
    w.run()
    assert w.ok and not w.errors, w.errors
    assert ("image", "E01-S001_first", True) in calls
    assert ("video", "E01-S001") in calls
    assert "确认设定图" in approvals
    assert "确认成片动态与声音" in approvals
    assert list((store.root / "retro").glob("*.md"))
    assert (store.root / "review" / "final_ep001.md").is_file()


def test_recover_tasks_claims_finished_and_clears_dead(store, monkeypatch):
    """查询并认领：成功任务下载进片段目录并写 manifest；没拿到任务号的清记录。"""
    from src.novel_writer.core.seedance import recover_tasks
    from src.novel_writer.core.seedance_tasks import SeedanceTasks

    tasks = SeedanceTasks(store.root)
    with tasks.locked() as records:
        records["1:E01-S001"] = {"input_hash": "hash-1", "task_id": "task-9", "state": "submitted"}
        records["1:E01-S002"] = {"input_hash": "", "state": "submitting"}
        tasks.save(records)

    real_client = httpx.Client

    def handler(req):
        if req.method == "GET" and "task-9" in str(req.url):
            return httpx.Response(200, json={
                "status": "succeeded", "content": {"video_url": "https://files.invalid/v"}})
        return httpx.Response(200, content=b"\x00\x00\x00\x18ftypisom")

    monkeypatch.setattr(
        "src.novel_writer.core.seedance.httpx.Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler)),
    )
    cfg = {"api_key": "k", "model": "doubao-seedance-2-0-mini-260615"}
    lines = recover_tasks(cfg, store)
    assert any("已认领" in line for line in lines)
    assert any("记录已清" in line for line in lines)

    clip = store.episode_clip_dir(1) / "E01-S001.mp4"
    assert clip.is_file()
    manifest = json.loads((store.videos_dir / "ep001_clips.json").read_text(encoding="utf-8"))
    assert manifest["E01-S001"]["signature"] == "hash-1"
    assert manifest["E01-S001"]["recovered"] is True
    records = json.loads(SeedanceTasks(store.root).path.read_text(encoding="utf-8"))
    assert records["1:E01-S001"]["state"] == "completed"
    assert "1:E01-S002" not in records


def test_recover_tasks_reports_still_running(store, monkeypatch):
    from src.novel_writer.core.seedance import recover_tasks
    from src.novel_writer.core.seedance_tasks import SeedanceTasks

    tasks = SeedanceTasks(store.root)
    with tasks.locked() as records:
        records["1:E01-S001"] = {"input_hash": "h", "task_id": "task-1", "state": "submitting"}
        tasks.save(records)

    real_client = httpx.Client

    def handler(req):
        return httpx.Response(200, json={"status": "running"})

    monkeypatch.setattr(
        "src.novel_writer.core.seedance.httpx.Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler)),
    )
    lines = recover_tasks({"api_key": "k", "model": "doubao-seedance-2-0-mini-260615"}, store)
    assert any("仍在生成" in line for line in lines)
    records = json.loads(SeedanceTasks(store.root).path.read_text(encoding="utf-8"))
    assert records["1:E01-S001"]["state"] == "submitted"


def test_recover_tasks_without_pending_reports_empty(store):
    from src.novel_writer.core.seedance import recover_tasks

    lines = recover_tasks({"api_key": "k"}, store)
    assert lines == ["没有未完成的 Seedance 云端任务"]
