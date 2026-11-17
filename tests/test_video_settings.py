"""视频生成 API 设置页：保存工作流/帧率，拒绝无效工作流。"""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from src.novel_writer.ui.settings.video_dialog import VideoDialog  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _workflow(tmp_path):
    path = tmp_path / "video_api.json"
    path.write_text(json.dumps({
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{positive}}"}},
        "2": {"class_type": "EmptyLatentImage",
              "inputs": {"width": "{{width}}", "height": "{{height}}"}},
    }), encoding="utf-8")
    return path


def test_video_dialog_saves_workflow_and_fps(qapp, tmp_path):
    wf = _workflow(tmp_path)
    dialog = VideoDialog({})
    dialog.base_url.setText("http://127.0.0.1:8188/")
    dialog.workflow.setText(str(wf))
    dialog.fps.setValue(24)
    dialog.length.setValue(4)
    dialog._save()

    cfg = dialog.get_config()["video_generation"]
    assert cfg["base_url"] == "http://127.0.0.1:8188"
    assert cfg["workflow"] == str(wf)
    assert cfg["fps"] == 24 and cfg["length"] == 4


def test_video_dialog_rejects_workflow_without_placeholders(qapp, tmp_path, monkeypatch):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"1": {"class_type": "X", "inputs": {}}}), encoding="utf-8")
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: warnings.append(args))

    dialog = VideoDialog({})
    dialog.base_url.setText("http://127.0.0.1:8188")
    dialog.workflow.setText(str(path))
    dialog._save()

    assert warnings and "占位符" in str(warnings[0][2])
    assert "video_generation" not in dialog.get_config()
