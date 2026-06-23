"""workflow 模块的单元测试。"""
import pytest
from pathlib import Path
from src.novel_writer.core.workflow import (
    WorkflowMode, ContextTier, WorkflowStep, WorkflowDef,
    WorkflowRunner, WorkflowError, build_workflow,
    DEFAULT_WORKFLOW, CONTINUE_WORKFLOW, VALIDATE_WORKFLOW,
    STEP_CONTEXT_TIERS
)


@pytest.fixture
def project_info():
    """项目信息。"""
    return {
        "title": "测试小说",
        "genre": "玄幻",
        "style": "轻松幽默",
        "target_chapters": 10
    }


@pytest.fixture
def agents():
    """模拟智能体。"""
    from unittest.mock import MagicMock
    from src.novel_writer.core.agents.base import BaseAgent, AgentConfig
    from src.novel_writer.core.llm.base import LLMResponse

    class MockAgent(BaseAgent):
        def __init__(self, config):
            self.config = config
            self.history = []

        async def run(self, user_input, context=""):
            return LLMResponse(content="测试响应")

    config = AgentConfig(
        name="test_agent",
        role="writer",
        title="测试智能体",
        system_prompt="测试提示词",
        skills=["正文写作", "剧情审查"]
    )
    return {"test_agent": MockAgent(config)}


@pytest.fixture
def project_dir(tmp_path):
    """创建临时项目目录。"""
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    (project_dir / "planning").mkdir()
    (project_dir / "chapters").mkdir()
    (project_dir / "review").mkdir()
    return project_dir


class TestWorkflowMode:
    """WorkflowMode 测试类。"""

    def test_new_book(self):
        """测试新书模式。"""
        assert WorkflowMode.NEW_BOOK.value == "new_book"

    def test_continue(self):
        """测试续写模式。"""
        assert WorkflowMode.CONTINUE.value == "continue"

    def test_fill_gaps(self):
        """测试查漏补缺模式。"""
        assert WorkflowMode.FILL_GAPS.value == "fill_gaps"

    def test_validate(self):
        """测试校验模式。"""
        assert WorkflowMode.VALIDATE.value == "validate"


class TestContextTier:
    """ContextTier 测试类。"""

    def test_global(self):
        """测试全局层级。"""
        assert ContextTier.GLOBAL.value == 0

    def test_world(self):
        """测试世界观层级。"""
        assert ContextTier.WORLD.value == 1

    def test_narrative(self):
        """测试叙事层级。"""
        assert ContextTier.NARRATIVE.value == 2

    def test_working(self):
        """测试工作层级。"""
        assert ContextTier.WORKING.value == 3


class TestWorkflowStep:
    """WorkflowStep 测试类。"""

    def test_default_values(self):
        """测试默认值。"""
        step = WorkflowStep()
        assert step.id == ""
        assert step.needs == ""
        assert step.prompt == ""
        assert step.input_files == []
        assert step.output == ""
        assert step.repeat == 0
        assert step.every == 0
        assert step.optional == False
        assert step.update_planning == False

    def test_custom_values(self):
        """测试自定义值。"""
        step = WorkflowStep(
            id="chapter",
            needs="正文写作",
            prompt="写第{n}章",
            input_files=["大纲.md"],
            output="chapters/{n}.txt",
            repeat=10,
            every=1,
            optional=True,
            update_planning=True
        )
        assert step.id == "chapter"
        assert step.needs == "正文写作"
        assert step.prompt == "写第{n}章"
        assert step.input_files == ["大纲.md"]
        assert step.output == "chapters/{n}.txt"
        assert step.repeat == 10
        assert step.every == 1
        assert step.optional == True
        assert step.update_planning == True


class TestWorkflowDef:
    """WorkflowDef 测试类。"""

    def test_from_dict(self):
        """测试从字典创建。"""
        data = {
            "name": "测试工作流",
            "description": "测试描述",
            "project": {"title": "测试"},
            "steps": [
                {"id": "chapter", "needs": "正文写作", "prompt": "写第{n}章"}
            ]
        }
        wf = WorkflowDef.from_dict(data)
        assert wf.name == "测试工作流"
        assert wf.description == "测试描述"
        assert wf.project == {"title": "测试"}
        assert len(wf.steps) == 1
        assert wf.steps[0].id == "chapter"

    def test_to_dict(self):
        """测试转换为字典。"""
        step = WorkflowStep(id="chapter", needs="正文写作")
        wf = WorkflowDef(name="测试", steps=[step])
        data = wf.to_dict()
        assert data["name"] == "测试"
        assert len(data["steps"]) == 1
        assert data["steps"][0]["id"] == "chapter"


class TestWorkflowRunner:
    """WorkflowRunner 测试类。"""

    def test_init(self, agents, project_dir, project_info):
        """测试初始化。"""
        runner = WorkflowRunner(agents, project_dir, project_info)
        assert runner.agents == agents
        assert runner.project_dir == project_dir
        assert runner.project_info == project_info

    def test_find_agent(self, agents, project_dir, project_info):
        """测试查找智能体。"""
        runner = WorkflowRunner(agents, project_dir, project_info)
        agent = runner.find_agent("正文写作")
        assert agent is not None
        assert agent.config.name == "test_agent"

    def test_find_agent_not_found(self, agents, project_dir, project_info):
        """测试查找不存在的智能体。"""
        runner = WorkflowRunner(agents, project_dir, project_info)
        agent = runner.find_agent("不存在的技能")
        assert agent is None

    def test_stop(self, agents, project_dir, project_info):
        """测试停止信号。"""
        runner = WorkflowRunner(agents, project_dir, project_info)
        assert runner._stop == False
        runner.stop()
        assert runner._stop == True


class TestBuildWorkflow:
    """build_workflow 测试类。"""

    def test_new_book(self, project_info):
        """测试新书模式。"""
        wf = build_workflow(WorkflowMode.NEW_BOOK, project_info, 1, 10)
        assert wf.name == "自动写作"
        assert len(wf.steps) > 0
        assert wf.project["title"] == "测试小说"

    def test_continue(self, project_info):
        """测试续写模式。"""
        wf = build_workflow(WorkflowMode.CONTINUE, project_info, 5, 10)
        assert wf.name == "续写"
        assert len(wf.steps) > 0

    def test_validate(self, project_info):
        """测试校验模式。"""
        wf = build_workflow(WorkflowMode.VALIDATE, project_info, 1, 10)
        assert wf.name == "校验"
        assert len(wf.steps) > 0

    def test_chapter_repeat(self, project_info):
        """测试章节循环设置。"""
        wf = build_workflow(WorkflowMode.NEW_BOOK, project_info, 1, 20)
        chapter_step = None
        for step in wf.steps:
            if step.id == "chapter":
                chapter_step = step
                break
        assert chapter_step is not None
        assert chapter_step.repeat == 20

    def test_continue_start_chapter(self, project_info):
        """测试续写模式的起始章节。"""
        wf = build_workflow(WorkflowMode.CONTINUE, project_info, 5, 10)
        assert wf.project.get("_start_chapter") == 5


class TestStepContextTiers:
    """STEP_CONTEXT_TIERS 测试类。"""

    def test_chapter_tier(self):
        """测试章节写作的上下文层级。"""
        assert STEP_CONTEXT_TIERS["chapter"] == ContextTier.WORKING

    def test_polish_tier(self):
        """测试润色的上下文层级。"""
        assert STEP_CONTEXT_TIERS["polish"] == ContextTier.NARRATIVE

    def test_proofread_tier(self):
        """测试校对的上下文层级。"""
        assert STEP_CONTEXT_TIERS["proofread"] == ContextTier.GLOBAL

    def test_quality_check_tier(self):
        """测试质量检查的上下文层级。"""
        assert STEP_CONTEXT_TIERS["quality_check"] == ContextTier.GLOBAL

    def test_toc_tier(self):
        """测试目录的上下文层级。"""
        assert STEP_CONTEXT_TIERS["toc"] == ContextTier.GLOBAL

    def test_review_tier(self):
        """测试审核的上下文层级。"""
        assert STEP_CONTEXT_TIERS["review"] == ContextTier.NARRATIVE
