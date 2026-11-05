"""新建项目对话框的 AI 润色回归：空正文不能冲掉草稿、本地模型必须关思考。"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def dialog(qapp, monkeypatch):
    from src.novel_writer.ui.project.new_project_dialog import NewProjectDialog

    # 离屏环境没有真弹窗，模态框会把测试卡死
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    dlg = NewProjectDialog()
    yield dlg
    dlg.close()


class _StubLLM:
    base_url = "http://localhost:8080/v1"


class _StubAgent:
    """只回放固定分片，记录调用参数。"""

    llm = _StubLLM()

    def __init__(self, chunks):
        self.chunks = chunks
        self.seen: dict = {}

    async def stream_run(self, prompt, thinking=None, keep_history=True, **_kw):
        self.seen = {"thinking": thinking, "keep_history": keep_history, "prompt": prompt}
        for kind, text in self.chunks:
            yield kind, text


class TestEmptyResultGuard:
    def test_empty_body_keeps_draft(self, dialog):
        """模型没吐正文时绝不能清空用户草稿（曾经的 bug：清空还提示成功）。"""
        dialog.theme_edit.setPlainText("原草稿：天幕盘点，权谋渡段子")
        dialog._on_ai_done("theme", "  \n ")
        assert dialog.theme_edit.toPlainText() == "原草稿：天幕盘点，权谋渡段子"
        assert "草稿已保留" in dialog._status_label.text()

    def test_formatted_body_replaces_draft(self, dialog):
        dialog.theme_edit.setPlainText("原草稿")
        dialog._on_ai_done(
            "theme",
            "【核心主题】天幕直播\n【立意】帝王功过被天幕直播盘点\n"
            "【卖点】帝王盘点，权谋破局\n【读者】历史权谋爱好者",
        )
        text = dialog.theme_edit.toPlainText()
        assert "【核心主题】天幕直播" in text
        assert "【读者】历史权谋爱好者" in text
        assert "原草稿" not in text

    def test_long_section_with_wrapped_lines_is_kept(self, dialog):
        """600~800 字输出会换行：续行必须接回上一节，不能被逐行丢掉。"""
        dialog._on_ai_done(
            "theme",
            "【核心主题】天幕直播，权谋渡劫\n"
            "【立意】第一行讲主线。\n第二行讲阻力。\n第三行讲根本矛盾。\n"
            "（总字数：约650字）\n"
            "【卖点】帝王盘点、权谋破局\n【读者】历史权谋爱好者",
        )
        text = dialog.theme_edit.toPlainText()
        assert "第二行讲阻力。" in text
        assert "第三行讲根本矛盾。" in text
        assert "总字数" not in text

    def test_other_field_sections_are_dropped(self, dialog):
        dialog._on_ai_done("theme", "【核心主题】甲\n【整体构思】不该出现\n【读者】乙")
        text = dialog.theme_edit.toPlainText()
        assert "整体构思" not in text
        assert "【读者】乙" in text

    def test_new_long_form_sections_are_kept(self, dialog):
        """加长后的六个小节都要保留，不能被字段过滤吃掉。"""
        dialog._on_ai_done(
            "theme",
            "【核心主题】甲\n【立意】乙\n【世界观钩子】丙\n"
            "【卖点】丁、戊\n【读者】己\n【一句话简介】庚",
        )
        text = dialog.theme_edit.toPlainText()
        for key in ("世界观钩子", "一句话简介"):
            assert f"【{key}】" in text

    def test_direction_sections_are_kept(self, dialog):
        dialog._on_ai_done(
            "direction",
            "【整体构思】甲\n【卷章安排】开局(10章)：乙\n【节奏】丙\n"
            "【人物弧线】丁\n【预估字数】约20万字",
        )
        text = dialog.direction_edit.toPlainText()
        assert "【卷章安排】开局(10章)：乙" in text
        assert "【预估字数】约20万字" in text

    def test_incomplete_output_gets_hint(self, dialog):
        """模型只写两节就收尾时，状态条要提示补全而不是假装完整。"""
        dialog._on_ai_done("theme", "【核心主题】甲\n【立意】乙")
        status = dialog._status_label.text()
        assert "缺【世界观钩子】" in status
        assert "再点一次" in status

    def test_single_stage_plan_gets_hint(self, dialog):
        dialog._on_ai_done(
            "direction",
            "【整体构思】甲\n【卷章安排】开局(10章)：乙\n【节奏】丙\n"
            "【人物弧线】丁\n【预估字数】约20万字",
        )
        assert "卷章安排只写了 1 段" in dialog._status_label.text()

    def test_complete_plan_has_no_hint(self, dialog):
        dialog._on_ai_done(
            "direction",
            "【整体构思】甲\n【卷章安排】开局(10章)：乙\n秦皇(10章)：丙\n"
            "【节奏】丁\n【人物弧线】戊\n【预估字数】约20万字",
        )
        assert "再点一次" not in dialog._status_label.text()

    def test_alternate_stage_formats_count_as_multiple(self, dialog):
        """模型常写「第一阶段（1-10 章）」：不能误判成只写了一段。"""
        dialog._on_ai_done(
            "direction",
            "【整体构思】甲\n【卷章安排】第一阶段（1-10 章）：乙\n第二阶段（11-20 章）：丙\n"
            "【节奏】丁\n【人物弧线】戊\n【预估字数】约20万字",
        )
        assert "卷章安排只写了 1 段" not in dialog._status_label.text()


class TestAIWorker:
    def test_turns_thinking_off(self, qapp):
        """结构化短改写不该开思考：本地模型会把额度全花在思考上，正文 0 字。"""
        from src.novel_writer.ui.project.new_project_dialog import _AIWorker

        agent = _StubAgent([("thinking", "先想想…"), ("text", "【核心主题】天幕直播")])
        got: list[str] = []
        worker = _AIWorker(agent, "润色")
        worker.completed.connect(got.append)
        worker.run()

        assert agent.seen["thinking"] == "off"
        assert agent.seen["keep_history"] is False
        assert got == ["【核心主题】天幕直播"]

    def test_no_answer_text_emits_error(self, qapp):
        """只有思考没有正文：报错而不是回填空串。"""
        from src.novel_writer.ui.project.new_project_dialog import _AIWorker

        agent = _StubAgent([("thinking", "想了很久…")])
        errors: list[str] = []
        worker = _AIWorker(agent, "润色")
        worker.error.connect(errors.append)
        worker.run()

        assert errors and "没有输出正文" in errors[0]

    def test_timeout_returns_instead_of_hanging(self, qapp):
        """本地模型卡住时必须按超时返回，不能一直锁着按钮。"""
        import asyncio

        from src.novel_writer.ui.project.new_project_dialog import _AIWorker

        class _SlowAgent:
            llm = _StubLLM()

            async def stream_run(self, prompt, thinking=None, keep_history=True, **_kw):
                await asyncio.sleep(30)
                yield "text", "迟到"

        worker = _AIWorker(_SlowAgent(), "润色")
        worker._timeout = 0.3
        errors: list[str] = []
        worker.error.connect(errors.append)
        worker.run()

        assert errors and "超时" in errors[0]


class TestFormLayout:
    def test_voice_and_meme_rows_not_duplicated(self, dialog):
        """合并回归：两边都加过叙述语言/梗密度，曾各渲染成两行下拉。"""
        from PySide6.QtWidgets import QLabel

        from src.novel_writer.core.meme_style import DEFAULT_MEME_LEVEL
        from src.novel_writer.core.narrative_voice import DEFAULT_VOICE

        labels = [w.text() for w in dialog.findChildren(QLabel)]
        assert labels.count("叙述语言") == 1
        assert labels.count("梗密度") == 1
        assert dialog.voice_combo.currentText() == DEFAULT_VOICE
        assert dialog.meme_combo.currentText() == DEFAULT_MEME_LEVEL
