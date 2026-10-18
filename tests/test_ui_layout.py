"""主窗口布局回归：两级导航、专注模式、状态条。"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from src.novel_writer.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_main_window_layout_and_focus_mode(qapp):
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    qapp.processEvents()

    assert w.editor.tab_bar.count() == 2
    assert w.editor.planning_list.count() == 9
    assert w.splitter.count() == 3

    w._toggle_focus_mode(True)
    qapp.processEvents()
    assert w._focus_mode is True
    assert not w.sidebar.isVisible()
    assert not w.agent_panel.isVisible()
    assert w._focus_action.isChecked()

    w._toggle_focus_mode(False)
    qapp.processEvents()
    assert w._focus_mode is False
    assert w.sidebar.isVisible()
    assert w.agent_panel.isVisible()
    assert not w._focus_action.isChecked()
    w.close()


def test_agent_strip_caps_at_two_rows(qapp):
    from src.novel_writer.ui.agent.office_scene import OfficeScene
    from PySide6.QtCore import Qt

    office = OfficeScene()
    office.setup_agents({f"agent{i}": {"title": f"角色{i}"} for i in range(12)}, {})
    assert office.height() == 182  # 两行上限，不无限长高
    assert office._scroll.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded

    office.setup_agents({f"agent{i}": {"title": f"角色{i}"} for i in range(3)}, {})
    assert office.height() == 104
    assert office._scroll.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
