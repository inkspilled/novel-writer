"""速度档位回归：辅助步骤降频、正文/章后抽取不受影响、项目记住选择。"""
from __future__ import annotations

import asyncio
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from src.novel_writer.core.agents.base import (  # noqa: E402
    AgentConfig,
    BaseAgent,
    reset_model_override,
    set_model_override,
)
from src.novel_writer.core.llm.base import BaseLLM, LLMResponse  # noqa: E402
from src.novel_writer.core.workflow import (  # noqa: E402
    DEFAULT_WORKFLOW,
    WorkflowDef,
    WorkflowMode,
    WorkflowRunner,
    WorkflowStep,
    build_workflow,
)


INFO = {"title": "测试书", "genre": "玄幻", "style": "热血"}


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _by_id(wf):
    return {s.id: s for s in wf.steps}


class TestSpeedProfiles:
    def test_standard_keeps_dense_schedule(self):
        by = _by_id(build_workflow(WorkflowMode.NEW_BOOK, INFO, 1, 100))
        assert by["proofread"].every == 1
        assert by["sim"].every == 1
        assert by["sim"].thinking == "on"
        assert by["chapter_digest"].every == 1

    def test_fast_lowers_only_aux_steps(self):
        by = _by_id(build_workflow(
            WorkflowMode.NEW_BOOK, INFO, 1, 100, speed_profile="fast",
        ))
        assert by["proofread"].every == 5
        assert by["sim"].every == 2
        assert by["sim"].thinking == "off"
        assert by["sim"].offset == 1  # 从第 1 章开始 → 1/3/5… 有推演
        assert by["inspiration"].every == 6
        assert by["summary"].every == 10
        assert by["update_chapter_outline"].every == 10
        assert by["update_characters"].every == 20
        assert by["review_panel"].every == 20

        # 正文、润色、章后抽取不受速度档位影响
        assert by["chapter"].every == 0 and by["chapter"].max_tokens == 8192
        assert by["polish"].every == 2
        assert by["chapter_digest"].every == 1
        assert by["chapter_digest"].parallel_ok is True

    def test_fast_profile_does_not_mutate_template(self):
        build_workflow(WorkflowMode.NEW_BOOK, INFO, 1, 20, speed_profile="fast")
        template = {s["id"]: s for s in DEFAULT_WORKFLOW["steps"]}
        assert template["proofread"]["every"] == 1
        assert template["sim"]["every"] == 1
        assert template["sim"]["thinking"] == "on"

    def test_fast_sim_aligns_to_run_start(self):
        """续写从第 40 章开始 → 40/42… 有推演；从 41 开始 → 41/43…。"""
        by = _by_id(build_workflow(
            WorkflowMode.CONTINUE, INFO, 40, 60, speed_profile="fast",
        ))
        assert by["sim"].offset == 0
        by = _by_id(build_workflow(
            WorkflowMode.CONTINUE, INFO, 41, 61, speed_profile="fast",
        ))
        assert by["sim"].offset == 1

    def test_periodic_due_honors_offset(self):
        from src.novel_writer.core.workflow import WorkflowRunner, WorkflowStep

        step = WorkflowStep(id="sim", every=2, offset=1)
        assert WorkflowRunner._periodic_due(step, 1) is True
        assert WorkflowRunner._periodic_due(step, 2) is False
        assert WorkflowRunner._periodic_due(step, 3) is True
        assert WorkflowRunner._periodic_due(WorkflowStep(id="x"), 5) is False

    def test_validate_proofread_stays_single_pass(self):
        """校验模式的 proofread 不是周期步骤，快速档不能把它改成每 5 章。"""
        by = _by_id(build_workflow(
            WorkflowMode.VALIDATE, INFO, 1, 10, speed_profile="fast",
        ))
        assert by["proofread"].every == 0

    def test_unknown_profile_falls_back(self):
        by = _by_id(build_workflow(
            WorkflowMode.NEW_BOOK, INFO, 1, 100, speed_profile="turbo",
        ))
        assert by["proofread"].every == 1


class TestProjectSpeedPersistence:
    def test_project_roundtrip(self, tmp_path):
        from src.novel_writer.models.project import Project

        project = Project(title="测试书", speed_profile="fast")
        project.set_project_dir(tmp_path)
        project.save()

        loaded = Project.load(tmp_path)
        assert loaded.speed_profile == "fast"

    def test_old_meta_defaults_to_standard(self, tmp_path):
        from src.novel_writer.models.project import Project

        project = Project(title="旧书")
        project.set_project_dir(tmp_path)
        project.save()
        meta = tmp_path / "meta.json"
        import json
        data = json.loads(meta.read_text(encoding="utf-8"))
        data.pop("speed_profile", None)
        meta.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        assert Project.load(tmp_path).speed_profile == "standard"


class TestWorkflowDialog:
    def test_returns_selected_speed(self, qapp, tmp_path):
        from src.novel_writer.ui.project.project_dialogs import WorkflowModeDialog

        dialog = WorkflowModeDialog(tmp_path, 100, default_speed="fast")
        assert dialog._speed_combo.currentData() == "fast"
        assert dialog.get_result()[3] == "fast"

        # 规划模式不显示速度档位；切到新书全流程后显示
        dialog._mode_combo.setCurrentIndex(
            dialog._mode_combo.findData(WorkflowMode.NEW_BOOK)
        )
        assert dialog._speed_group.isVisibleTo(dialog)
        dialog._mode_combo.setCurrentIndex(
            dialog._mode_combo.findData(WorkflowMode.VALIDATE)
        )
        assert not dialog._speed_group.isVisibleTo(dialog)
        dialog.close()


# ── 步骤级快模型 ──

class RecordingLLM(BaseLLM):
    """记录每次调用实际使用的模型名。"""

    def __init__(self):
        super().__init__(model="main-model")
        self.models: list[str] = []

    async def chat(self, messages, temperature=0.7, max_tokens=4096,
                   thinking="auto", tools=None, model=None):
        self.models.append(model or self.model)
        return LLMResponse(content="正文内容测试", usage={})

    async def stream_chat(self, messages, temperature=0.7, max_tokens=4096,
                          thinking="auto", tools=None, model=None):
        self.models.append(model or self.model)
        yield "text", "正文内容测试"


class DummyToolkit:
    def schemas(self):
        return []

    def names(self):
        return []

    async def execute(self, name, args):
        return ""


def make_agent() -> tuple[BaseAgent, RecordingLLM]:
    llm = RecordingLLM()
    agent = BaseAgent(
        AgentConfig(name="a", role="r", title="a", system_prompt="s", skills=["x"]),
        llm,
    )
    return agent, llm


class TestFastModelRouting:
    def test_templates_mark_aux_steps_fast(self):
        by = _by_id(build_workflow(WorkflowMode.NEW_BOOK, INFO, 1, 100))
        for sid in ("sim", "chapter_digest", "proofread", "summary", "percept_extract"):
            assert by[sid].model == "fast", sid
        for sid in ("chapter", "polish", "review", "update_characters"):
            assert by[sid].model == "", sid

    def test_resolve_model(self, tmp_path):
        runner = WorkflowRunner(
            agents={}, project_dir=tmp_path,
            project_info={"title": "书", "fast_model": "flash"},
        )
        assert runner.resolve_model(WorkflowStep(id="a")) is None
        assert runner.resolve_model(WorkflowStep(id="b", model="fast")) == "flash"
        assert runner.resolve_model(WorkflowStep(id="c", model="custom")) == "custom"

        runner2 = WorkflowRunner(
            agents={}, project_dir=tmp_path, project_info={"title": "书"},
        )
        assert runner2.resolve_model(WorkflowStep(id="d", model="fast")) is None

    def test_agent_run_uses_explicit_and_context_model(self):
        agent, llm = make_agent()
        asyncio.run(agent.run("x", model="fast-explicit"))
        assert llm.models[-1] == "fast-explicit"

        token = set_model_override("fast-context")
        try:
            asyncio.run(agent.run("x"))
            assert llm.models[-1] == "fast-context"
        finally:
            reset_model_override(token)
        asyncio.run(agent.run("x"))
        assert llm.models[-1] == "main-model"

    def test_stream_and_tool_loop_use_model(self):
        agent, llm = make_agent()

        async def collect():
            return [chunk async for chunk in agent.stream_run("x", model="stream-fast")]

        asyncio.run(collect())
        assert llm.models[-1] == "stream-fast"

        asyncio.run(agent.run_with_tools(
            DummyToolkit(), "x", max_rounds=0, model="loop-fast",
        ))
        assert llm.models[-1] == "loop-fast"

    def test_runner_routes_per_step(self, tmp_path):
        """同一 Agent：普通步骤用主模型，标了 fast 的步骤用快模型。"""
        (tmp_path / "planning").mkdir()
        agent, llm = make_agent()
        runner = WorkflowRunner(
            agents={"a": agent}, project_dir=tmp_path,
            project_info={"title": "书", "fast_model": "flash"},
            enable_tools=False,
        )
        wf = WorkflowDef(
            name="模型路由",
            project={"title": "书"},
            steps=[
                WorkflowStep(id="plan_a", needs="x", prompt="A", output="planning/a.md"),
                WorkflowStep(id="plan_b", needs="x", prompt="B", output="planning/b.md",
                             model="fast"),
            ],
        )
        asyncio.run(runner.run(wf, {}))
        assert llm.models == ["main-model", "flash"]
