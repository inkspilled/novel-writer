import asyncio

import pytest

from src.novel_writer.core.agents.loop import AgentLoop
from src.novel_writer.core.agents.tools import ToolRegistry, Tool
from src.novel_writer.core.llm.base import LLMResponse
from src.novel_writer.core.location_identity import validate_geometry, location_distance, duplicate_candidates
from src.novel_writer.core.world_state import WorldState
from tests.test_agent_runtime import FakeLLM, make_agent


def test_tool_result_survives_empty_final_answer_recovery():
    toolkit = ToolRegistry()
    toolkit.register(Tool("get_open_loops", "伏笔", {}, lambda args: "钥匙在李四手中"))
    llm = FakeLLM([
        LLMResponse(content="", tool_calls=[{"id": "c1", "type": "function", "function": {"name": "get_open_loops", "arguments": "{}"}}], usage={"total_tokens": 2}),
        LLMResponse(content="<think>需要构思转折</think>", usage={"total_tokens": 3}),
        LLMResponse(content="李四交出假钥匙，真钥匙留在袖中。", usage={"total_tokens": 5}),
    ])
    result = asyncio.run(AgentLoop(make_agent(llm), toolkit, max_rounds=1).run("提供灵感"))
    assert "真钥匙" in result.content
    assert result.usage["total_tokens"] == 10
    last = llm.calls[-1]
    assert last["tools"] is None
    assert any(m.role == "tool" and "李四" in m.content for m in last["messages"])
    assert "可用工具：" not in last["messages"][0].content


def test_optional_inspiration_recovers_before_writing_output(tmp_path):
    from src.novel_writer.core.workflow import WorkflowRunner, WorkflowStep
    llm = FakeLLM([LLMResponse(content="<think>思考一下</think>"), LLMResponse(content="1.让来客冒用旧身份。")])
    agent = make_agent(llm)
    agent.config.skills = ["灵感激发"]
    runner = WorkflowRunner({"writer": agent}, tmp_path, {"title": "测试"}, enable_tools=False)
    errors = []
    runner.on_error = lambda *args: errors.append(args)
    step = WorkflowStep(id="inspiration", needs="灵感激发", prompt="提供三个转折", output="inspiration/{n}_灵感.md", optional=True)
    asyncio.run(runner._run_single(step, runner.project_info, 18, {}))
    assert not errors
    assert "冒用" in (tmp_path / "inspiration" / "18_灵感.md").read_text(encoding="utf-8")


def test_alias_update_does_not_create_location_or_false_movement(tmp_path):
    world = WorldState(tmp_path)
    world.load()
    world.add_location("青云宗山门", {"aliases": ["宗门入口"]})
    world.add_character("李四", {"location": "青云宗山门"})
    update = {"characters": {"李四": {"location": "宗门入口"}}, "locations": {"宗门入口": {"description": "石阶旁的入口"}}}
    compact = world.compact_update(update, 18)
    assert not compact.get("characters")
    world.apply_llm_update(update, 18)
    assert world.characters["李四"]["location"] == "青云宗山门"
    assert set(world.locations) == {"青云宗山门"}
    assert world.location_log == []


def test_location_merge_updates_all_references_and_persists_identity(tmp_path):
    world = WorldState(tmp_path)
    world.load()
    world.add_location("山门", {"description": "宗门入口"})
    world.add_location("宗门入口", {})
    world.add_character("李四", {"location": "宗门入口"})
    world.add_item("钥匙", {"location": "宗门入口"})
    world.record_location_event(2, "李四", "enter", "宗门入口")
    world.add_timeline_event(2, "归来", "宗门入口")
    world.merge_locations("宗门入口", "山门")
    identity = world.locations["山门"]["location_id"]
    world.sync_locations()
    world.save()
    loaded = WorldState(tmp_path)
    loaded.load()
    assert set(loaded.locations) == {"山门"}
    assert loaded.characters["李四"]["location"] == "山门"
    assert loaded.items_catalog["钥匙"]["location"] == "山门"
    assert loaded.location_log[0]["location"] == loaded.timeline[0]["location"] == "山门"
    assert loaded.locations["山门"]["location_id"] == identity
    assert loaded.canonical_location("宗门入口") == "山门"


def test_coordinates_require_frame_and_unit_and_never_merge_by_distance():
    with pytest.raises(ValueError):
        validate_geometry({"coordinates": [1, 2]})
    a = validate_geometry({"coordinates": [0, 0], "coordinate_frame": "青云宗", "coordinate_unit": "米"})
    b = validate_geometry({"coordinates": [3, 4], "coordinate_frame": "青云宗", "coordinate_unit": "米"})
    assert location_distance(a, b) == 5
    assert location_distance(a, {**b, "coordinate_frame": "魔域"}) is None
    assert location_distance(a, {**b, "coordinate_unit": "千米"}) is None
    from src.novel_writer.core.location_identity import validate_spatial_record
    with pytest.raises(ValueError, match="统一"):
        validate_spatial_record({"甲地": a}, "乙地", {**b, "coordinate_unit": "千米"})


def test_vector_candidates_respect_parent_areas():
    places = {"东门": {"description": "城墙的石门", "parent": "甲城"},
              "东城门": {"description": "城墙的石门", "parent": "甲城"},
              "南门": {"description": "城墙的石门", "parent": "乙城"}}
    assert [n for n, score in duplicate_candidates(places, "东门")] == ["东城门"]


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


def test_character_reload_reads_background_updates_without_overwriting(qapp, tmp_path):
    from src.novel_writer.core.characters.registry import CharacterRegistry
    from src.novel_writer.ui.character.character_page import CharacterPage
    registry = CharacterRegistry(tmp_path)
    registry.create("李四", chapter=1)
    registry.save()
    page = CharacterPage()
    page.set_project_dir(tmp_path)
    background = CharacterRegistry(tmp_path)
    rec = background.get("李四")
    from src.novel_writer.core.characters.registry import CharacterStatus
    rec.status = CharacterStatus.DEAD
    background.create("张三", chapter=18)
    background.save()
    world = WorldState(tmp_path)
    world.load()
    world.add_character("李四", {"location": "山门", "goals": ["守门"]})
    world.save()
    page.reload()
    assert page.list.count() == 2
    assert page.status_combo.currentData() == "dead"
    assert page.location.text() == "山门"
    assert CharacterRegistry(tmp_path).get("李四").status == CharacterStatus.DEAD
    page.close()


def test_character_edit_merges_with_new_background_fields(qapp, tmp_path):
    from src.novel_writer.core.characters.registry import CharacterRegistry, CharacterStatus
    from src.novel_writer.ui.character.character_page import CharacterPage
    registry = CharacterRegistry(tmp_path)
    registry.create("李四", chapter=1)
    registry.save()
    page = CharacterPage()
    page.set_project_dir(tmp_path)
    page.notes.setPlainText("作者新增备注")
    background = CharacterRegistry(tmp_path)
    background.get("李四").status = CharacterStatus.DEAD
    background.create("张三", chapter=18)
    background.save()
    page.reload()
    assert page.notes.toPlainText() == "作者新增备注"
    page.save()
    latest = CharacterRegistry(tmp_path)
    assert latest.get("李四").notes == "作者新增备注"
    assert latest.get("李四").status == CharacterStatus.DEAD
    assert latest.get("张三") is not None
    page.close()


def test_original_style_controls_roundtrip(qapp):
    from src.novel_writer.ui.project.new_project_dialog import NewProjectDialog
    dialog = NewProjectDialog(project_data={"title": "测试", "narrative_voice": "平实", "meme_level": "高"})
    assert dialog.voice_combo.currentText() == "平实"
    assert dialog.meme_combo.currentText() == "高"
    assert dialog.get_data()["narrative_voice"] == "平实"
    assert dialog.get_data()["meme_level"] == "高"
    dialog.close()


def test_location_page_refresh_and_edit_preserve_background_world(qapp, tmp_path):
    from src.novel_writer.ui.character.world_page import LocationPage
    world = WorldState(tmp_path)
    world.load()
    world.add_location("山门", {"description": "原来的石阶"})
    world.save()
    page = LocationPage()
    page.set_project_dir(tmp_path)
    world.add_location("山门", {"description": "石阶上留下了脚印"})
    world.save()
    page.reload()
    assert page.desc.toPlainText() == "石阶上留下了脚印"
    page.type.setText("关隘")
    world.add_character("张三", {"location": "山门"})
    world.save()
    page.reload()
    assert page.type.text() == "关隘"
    page.save()
    world.load()
    assert world.characters["张三"]["location"] == "山门"
    assert world.locations["山门"]["type"] == "关隘"
    page.close()


def test_unclosed_think_is_never_saved_as_content():
    from src.novel_writer.core.text_clean import clean_model_text
    assert clean_model_text("<think>没有完成的思考") == ""
    assert clean_model_text("<think>内部分析</think>\n真实内容") == "真实内容"


def test_workflow_keeps_style_and_operational_flags():
    from src.novel_writer.core.workflow import build_workflow, WorkflowMode
    workflow = build_workflow(WorkflowMode.NEW_BOOK,
        {"title": "测试", "narrative_voice": "平实", "meme_level": "中", "fast_model": "helper", "enable_offscreen_tick": False}, 1, 20)
    assert workflow.project["narrative_voice"] == "平实"
    assert workflow.project["fast_model"] == "helper"
    assert workflow.project["enable_offscreen_tick"] is False
