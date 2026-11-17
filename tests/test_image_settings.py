import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import base64
import json
import httpx
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage
from PySide6.QtCore import QBuffer, QIODevice
from src.novel_writer.core.image_generation import ImageAPIClient, config_from_app
from src.novel_writer.core.comfyui import ComfyError
from src.novel_writer.ui.settings.image_dialog import ImageDialog
from src.novel_writer.ui.agent.agent_dialog import AgentDialog

@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])

def png_bytes():
    image = QImage(2, 2, QImage.Format.Format_RGB32)
    image.fill(0xff123456)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())

def test_image_config_save_and_cancel(qapp):
    original = {"current_provider": {"model": "text-model"}}
    dialog = ImageDialog(original)
    dialog.base_url.setText("https://images.example/v1/")
    dialog.api_key.setText("test-key")
    dialog.model.setText("image-model")
    dialog.negative_check.setChecked(True)
    dialog._save()
    saved = dialog.get_config()
    assert "image_generation" not in original
    assert saved["current_provider"] == original["current_provider"]
    assert config_from_app(saved)["model"] == "image-model"
    assert config_from_app(saved)["supports_negative_prompt"] is True
    reopened = ImageDialog(saved)
    assert reopened.base_url.text() == "https://images.example/v1"
    assert reopened.api_key.text() == "test-key"
    reopened.model.setText("unsaved")
    reopened.reject()
    assert saved["image_generation"]["model"] == "image-model"

def test_legacy_comfy_config():
    cfg = config_from_app({"comfyui": {"workflow": "existing.json", "base_url": "http://localhost:8188"}})
    assert cfg["provider"] == "comfyui"
    assert cfg["workflow"] == "existing.json"

@pytest.mark.parametrize("response_kind", ["b64_json", "url"])
def test_image_api_decodes_and_downloads_without_forwarding_key(qapp, response_kind):
    data = png_bytes()
    def handle(request):
        if request.method == "POST":
            assert str(request.url) == "https://images.example/v1/images/generations"
            assert request.headers["Authorization"] == "Bearer secret"
            return httpx.Response(200, json={"data": [{response_kind: base64.b64encode(data).decode() if response_kind == "b64_json" else "https://cdn.example/image.png"}]})
        assert "Authorization" not in request.headers
        return httpx.Response(200, content=data)
    client = ImageAPIClient({"base_url": "https://images.example/v1", "model": "test", "api_key": "secret"})
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(handle))
    assert not QImage.fromData(client.generate("角色", "模糊")).isNull()
    client.close()

def test_api_error_does_not_expose_key():
    client = ImageAPIClient({"base_url": "https://images.example/v1", "model": "test", "api_key": "secret"})
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, text="secret")))
    with pytest.raises(ComfyError, match="401") as error:
        client.generate("test")
    assert "secret" not in str(error.value)
    client.close()


@pytest.mark.parametrize("supports,uses_field", [(True, True), (False, False)])
def test_negative_prompt_uses_field_or_documented_fallback(qapp, supports, uses_field):
    """支持负面词字段就单独传；纯 OpenAI 兼容接口才退回「避免：…」的已知降级。"""
    seen = {}
    data = png_bytes()

    def handle(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(data).decode()}]})

    cfg = {"base_url": "https://images.example/v1", "model": "test", "api_key": "k"}
    if supports:
        cfg["supports_negative_prompt"] = True
    client = ImageAPIClient(cfg)
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(handle))
    client.generate("正向提示词", "模糊，畸形手指")
    client.close()

    if uses_field:
        assert seen["negative_prompt"] == "模糊，畸形手指"
        assert "模糊" not in seen["prompt"]
    else:
        assert "negative_prompt" not in seen
        assert "避免：模糊，畸形手指" in seen["prompt"]

def test_agent_tabs_are_separate_and_runtime_backed(qapp, monkeypatch):
    from src.novel_writer.core.drama_agents import DRAMA_AGENT_CONFIGS
    import src.novel_writer.ui.agent.agent_dialog as module
    saved = []
    monkeypatch.setattr(module, "save_agents", lambda cfg: saved.append(dict(cfg)))
    dialog = AgentDialog({})
    novel_names = set(dialog._agents)
    assert dialog.domain_tabs.tabText(0) == "小说"
    assert dialog.domain_tabs.tabText(1) == "短剧"
    dialog.domain_tabs.setCurrentIndex(1)
    assert dialog.agent_list.count() == len(DRAMA_AGENT_CONFIGS)
    assert dialog.agent_prompt_input.toPlainText() in [v["system_prompt"] for v in DRAMA_AGENT_CONFIGS.values()]
    assert not dialog.agent_prompt_input.isEnabled()
    dialog._save()
    assert set(saved[0]) == novel_names
    dialog.domain_tabs.setCurrentIndex(0)
    assert set(dialog._agents) == novel_names


def test_worker_saves_generated_asset_and_reports_completion(qapp, tmp_path, monkeypatch):
    from src.novel_writer.ui.drama.comfy_worker import ComfyImageWorker
    from src.novel_writer.core import image_generation
    saved = []
    closed = []
    class Store:
        def __init__(self):
            # 生成账本 / 失败清单需要项目目录
            self.root = tmp_path / "drama"
            self.assets_dir = self.root / "assets"
        def asset_prompt(self, asset):
            return "角色参考图", "模糊"
        def audit_asset_prompt(self, asset):
            return []
        def save_generated_image(self, asset_id, data, expected_size=None):
            # 画幅核对参数按类型传入（无 kind 默认走人物 3:4 竖版）
            assert expected_size == (960, 1280)
            saved.append((asset_id, data))
            return "assets/generated/hero.png"
        def load_assets(self):
            return [{"id": "hero", "aspect_warning": ""}]
    class Client:
        def generate(self, positive, negative, prefix, width=1024, height=1024):
            assert positive == "角色参考图"
            # 画幅按类型传入（无 kind 默认走人物 3:4 竖版）
            assert (width, height) == (960, 1280)
            return b"generated"
        def close(self):
            closed.append(True)
    monkeypatch.setattr(image_generation, "create_client", lambda cfg: Client())
    worker = ComfyImageWorker(Store(), [{"id": "hero"}], {"provider": "api"})
    results = []
    worker.finished_ok.connect(results.append)
    worker.run()
    assert saved == [("hero", b"generated")]
    assert closed == [True]
    assert "1" in results[0]
    # 生成记录已落账本
    records = (tmp_path / "drama" / "generation_records.jsonl").read_text(encoding="utf-8")
    assert "asset:hero:plain" in records
