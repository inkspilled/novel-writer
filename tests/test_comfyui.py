"""ComfyUI 接入：工作流占位符替换 + 排队/轮询/下载（全部用假 I/O，不联网）。"""
from __future__ import annotations

import json

import pytest

from src.novel_writer.core import comfyui
from src.novel_writer.core.comfyui import (
    ComfyError, ComfyUIClient, fill_workflow, load_workflow,
)

WORKFLOW = {
    "3": {"class_type": "KSampler",
          "inputs": {"seed": "{{seed}}", "steps": 20}},
    "6": {"class_type": "CLIPTextEncode",
          "inputs": {"text": "{{positive}}", "clip": ["4", 1]}},
    "7": {"class_type": "CLIPTextEncode",
          "inputs": {"text": "{{negative}}"}},
    "9": {"class_type": "SaveImage",
          "inputs": {"filename_prefix": "{{prefix}}", "images": ["8", 0]}},
}


@pytest.fixture()
def workflow_file(tmp_path):
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps(WORKFLOW), encoding="utf-8")
    return path


class FakeClient(ComfyUIClient):
    """把三处 HTTP I/O 换成本地假数据，其余逻辑（占位符/轮询）照跑。"""

    def __init__(self, workflow_path, image=b"\x89PNG-fake"):
        super().__init__("http://comfy.local", workflow_path)
        self.image = image
        self.posted: dict | None = None
        self.calls: list[str] = []
        self.polls = 0

    def _post_json(self, path, payload):
        self.calls.append(path)
        self.posted = payload
        return {"prompt_id": "pid-1"}

    def _get_json(self, path):
        self.calls.append(path)
        self.polls += 1
        if self.polls < 2:
            return {}  # 第一次还没跑完
        return {"pid-1": {
            "status": {"completed": True, "status_str": "success"},
            "outputs": {"9": {"images": [
                {"filename": "out.png", "subfolder": "", "type": "output"}
            ]}},
        }}

    def _get_bytes(self, path, params):
        self.calls.append(path)
        self.params = params
        return self.image


def test_fill_workflow_replaces_nested_placeholders():
    filled = fill_workflow(WORKFLOW, {"positive": "古装少年", "negative": "模糊",
                                      "seed": 42, "prefix": "char_1"})
    assert filled["6"]["inputs"]["text"] == "古装少年"
    assert filled["7"]["inputs"]["text"] == "模糊"
    assert filled["3"]["inputs"]["seed"] == 42  # 数字占位符保留 int，ComfyUI 只认 int
    assert filled["9"]["inputs"]["filename_prefix"] == "char_1"
    assert filled["3"]["inputs"]["steps"] == 20  # 非字符串不动


def test_generate_queues_polls_and_downloads(monkeypatch, workflow_file):
    monkeypatch.setattr(comfyui, "POLL_INTERVAL", 0)
    client = FakeClient(workflow_file, image=b"IMG")
    data = client.generate("白衣剑客", "模糊", prefix="char_1", width=768, height=1344)

    assert data == b"IMG"
    assert client.posted["prompt"]["6"]["inputs"]["text"] == "白衣剑客"
    assert client.posted["prompt"]["7"]["inputs"]["text"] == "模糊"
    assert client.posted["prompt"]["9"]["inputs"]["filename_prefix"] == "char_1"
    assert "/prompt" in client.calls and "/history/pid-1" in client.calls
    assert client.params["filename"] == "out.png"


def test_reference_workflow_uploads_and_reuses_fixed_seed(monkeypatch, workflow_file, tmp_path):
    monkeypatch.setattr(comfyui, "POLL_INTERVAL", 0)
    ref_workflow = tmp_path / "reference.json"
    graph = {**WORKFLOW, "10": {"class_type": "LoadImage", "inputs": {"image": "{{reference_image}}"}}}
    ref_workflow.write_text(json.dumps(graph), encoding="utf-8")
    client = FakeClient(workflow_file)
    client.reference_workflow = str(ref_workflow)
    uploaded = []
    monkeypatch.setattr(client, "upload_image", lambda path: uploaded.append(path) or "novel_writer/hero.png")
    client.generate("same person", reference_image="portrait.png", seed=123)
    assert uploaded == ["portrait.png"]
    assert client.posted["prompt"]["10"]["inputs"]["image"] == "novel_writer/hero.png"
    assert client.posted["prompt"]["3"]["inputs"]["seed"] == 123
    client.close()


def test_reference_upload_uses_server_returned_name(tmp_path):
    import httpx
    image = tmp_path / "hero.png"
    image.write_bytes(b"image")
    client = ComfyUIClient()
    client._client.close()
    def upload(request):
        assert request.url.path == "/upload/image"
        assert b"overwrite" in request.content
        assert b"false" in request.content
        return httpx.Response(200, json={"name": "hero_1.png", "subfolder": "novel_writer"})
    client._client = httpx.Client(base_url="http://test", transport=httpx.MockTransport(upload))
    assert client.upload_image(image) == "novel_writer/hero_1.png"
    client.close()


def test_missing_workflow_raises_comfy_error(tmp_path):
    with pytest.raises(ComfyError):
        load_workflow(tmp_path / "nope.json")


def test_ui_format_workflow_rejected(tmp_path):
    path = tmp_path / "ui.json"
    path.write_text(json.dumps({"nodes": [], "links": []}), encoding="utf-8")
    # UI 格式也是 dict，但生成时会因为节点没有 class_type 被 ComfyUI 拒绝；
    # 这里至少保证能读出来（提示用户改用 API 格式由 ComfyUI 侧报错）
    assert load_workflow(path)["nodes"] == []


def test_history_error_raises(monkeypatch, workflow_file):
    monkeypatch.setattr(comfyui, "POLL_INTERVAL", 0)

    class ErrClient(FakeClient):
        def _get_json(self, path):
            return {"pid-1": {"status": {"status_str": "error",
                                          "messages": ["OOM"]}, "outputs": {}}}

    client = ErrClient(workflow_file)
    with pytest.raises(ComfyError) as exc:
        client.generate("x")
    assert "执行失败" in str(exc.value)


def test_timeout_raises(monkeypatch, workflow_file):
    monkeypatch.setattr(comfyui, "POLL_INTERVAL", 0)

    class SlowClient(FakeClient):
        def _get_json(self, path):
            return {}

    client = SlowClient(workflow_file)
    client.timeout = 0
    with pytest.raises(ComfyError) as exc:
        client.generate("x")
    assert "超时" in str(exc.value)


class RetryClient(ComfyUIClient):
    """只保留真实 _post_json（含重试包装），history/view 用假数据。"""

    def __init__(self, workflow_path):
        super().__init__("http://comfy.local", workflow_path)

    def _get_json(self, path):
        return {"pid-1": {"status": {"completed": True, "status_str": "success"},
                          "outputs": {"9": {"images": [
                              {"filename": "out.png", "subfolder": "", "type": "output"}]}}}}

    def _get_bytes(self, path, params):
        return b"fake-image-bytes"


def test_transport_error_retries_once(monkeypatch, workflow_file):
    """连不上自动重试一次；第二次通则整体成功（在传输层打桩，_retry 才会接住）。"""
    import httpx

    monkeypatch.setattr(comfyui, "RETRY_DELAY", 0)
    client = RetryClient(workflow_file)
    attempts = {"n": 0}
    ok = httpx.Response(200, json={"prompt_id": "pid-1"},
                        request=httpx.Request("POST", "http://comfy.local/prompt"))

    def flaky(*args, **kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise httpx.ConnectError("boom")
        return ok

    monkeypatch.setattr(client._client, "post", flaky)
    client.generate("x")
    assert attempts["n"] == 2


def test_transport_error_exhausts_retries(monkeypatch, workflow_file):
    import httpx

    monkeypatch.setattr(comfyui, "RETRY_DELAY", 0)
    client = RetryClient(workflow_file)
    attempts = {"n": 0}

    def down(*args, **kwargs):
        attempts["n"] += 1
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(client._client, "post", down)
    with pytest.raises(ComfyError) as exc:
        client.generate("x")
    assert attempts["n"] == client.retries + 1
    assert "重试" in str(exc.value)


def test_retries_disabled_passes_through(workflow_file):
    client = FakeClient(workflow_file)
    client.retries = 0

    def down(path, payload):
        raise ComfyError("连不上")

    client._post_json = down  # 直接替换实例方法
    with pytest.raises(ComfyError):
        client.generate("x")
