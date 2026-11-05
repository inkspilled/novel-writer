"""每 Agent 独立模型已移除：所有 Agent 共用主模型。"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_agent_config_has_no_model_field():
    from src.novel_writer.core.agents.base import AgentConfig

    assert "model" not in AgentConfig.__dataclass_fields__


def test_agents_config_has_no_model_keys():
    from src.novel_writer.core.agents import load_agents

    agents = load_agents()
    assert agents
    for name, info in agents.items():
        assert "model" not in info, name


def test_llm_mixin_shares_one_llm(monkeypatch):
    from src.novel_writer.ui.app_shell import llm_mixin

    monkeypatch.setattr(
        llm_mixin, "load_agents",
        lambda: {
            "a": {"title": "主编", "skills": ["选题"]},
            "b": {"title": "写手", "skills": ["正文写作"]},
        },
    )

    class Panel:
        def update_agent_buttons(self, cfg):
            self.cfg = cfg

    obj = llm_mixin.LlmMixin()
    obj.config = {}
    obj.llm = object()
    obj.agents = {}
    obj.agent_panel = Panel()

    obj._init_agents()

    assert set(obj.agents) == {"a", "b"}
    assert all(agent.llm is obj.llm for agent in obj.agents.values())


def test_agent_dialog_has_no_model_selector(qapp):
    from src.novel_writer.ui.agent.agent_dialog import AgentDialog

    dialog = AgentDialog({})
    assert not hasattr(dialog, "agent_model_combo")
    dialog.close()


def test_saved_agents_drop_legacy_model_key(qapp, monkeypatch):
    """即使 agents.json 还留旧 model 字段，保存时也要清掉。"""
    from src.novel_writer.ui.agent import agent_dialog

    legacy = {"a": {"title": "主编", "model": "old-model", "skills": []}}
    saved: dict = {}

    monkeypatch.setattr(agent_dialog, "load_agents", lambda: dict(legacy))
    monkeypatch.setattr(agent_dialog, "save_agents", lambda cfg: saved.update(cfg))

    dialog = agent_dialog.AgentDialog({})
    dialog._save()
    assert "model" not in saved["a"]
