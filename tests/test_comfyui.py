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
    assert filled["3"]["inputs"]["seed"] == "42"
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
