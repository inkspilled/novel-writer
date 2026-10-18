"""角色隔离回归 — 正典事件、生命周期、POV 视图与泄漏 canary。

这几条测试是“角色不能开上帝视角”的底线：
- 角色视图只包含 known_by 自己的事件；
- 别人的 hidden 字段永远不出现；
- 死亡角色知识冻结；
- 新角色不能看到入场前的事件；
- POV 提案的 knowledge_used 必须来自视图。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.novel_writer.core.canon.events import CanonLog, seed_event
from src.novel_writer.core.characters.registry import (
    CharacterRegistry,
    CharacterStatus,
    CharacterTier,
)
from src.novel_writer.core.characters.view import (
    CharacterViewBuilder,
    validate_knowledge_used,
)
from src.novel_writer.core.llm.base import BaseLLM, LLMResponse
from src.novel_writer.core.workflow import builtin_steps as bs
from src.novel_writer.core.workflow.definition import WorkflowStep


# ── 正典 ──

def test_canon_append_only_and_known_to(tmp_path: Path):
    canon = CanonLog(tmp_path)
    secret = canon.append(seed_event(
        1, "密室", "楚夜", "楚夜在密室觉醒", ["楚夜"],
        hidden="我是魔尊转世", visibility="private",
    ))
    public = canon.append(seed_event(
        1, "药圃", "楚夜", "楚夜从药圃离开", ["林牧", "楚夜"],
    ))
    future = canon.append(seed_event(
        9, "山门", "楚夜", "楚夜登上山门", ["林牧"],
    ))
    assert len(canon.all()) == 3
    assert canon.verify_chain()
    assert [e.id for e in canon.known_to("林牧", 5)] == [public.id]
    assert [e.id for e in canon.known_to("林牧", 10)] == [public.id, future.id]
    assert [e.id for e in canon.known_to("楚夜", 5)] == [secret.id, public.id]


def test_canon_tamper_detected(tmp_path: Path):
    canon = CanonLog(tmp_path)
    canon.append(seed_event(1, "药圃", "林牧", "林牧醒来", ["林牧"]))
    path = canon.path
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace("林牧醒来", "林牧被杀")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert CanonLog(tmp_path).verify_chain() is False


# ── 生命周期 ──

def test_registry_lifecycle_and_merge(tmp_path: Path):
    reg = CharacterRegistry(tmp_path)
    lin = reg.create("林牧", chapter=1, tier=CharacterTier.CORE, auto_sim=True,
                     aliases=["牧哥"], goals=["通过考核"])
    chu = reg.create("楚夜", chapter=1, tier=CharacterTier.RECURRING, auto_sim=True)
    assert reg.get("牧哥").id == lin.id
    assert reg.simulatable() and {c.name for c in reg.simulatable()} == {"林牧", "楚夜"}

    reg.mark_dead("楚夜", chapter=20)
    chu = reg.get("楚夜")
    assert chu.status == CharacterStatus.DEAD
    assert not chu.can_act and chu.auto_sim is False
    assert chu.knowledge_limit(25) == 20

    reg.resurrect("楚夜", chapter=30)
    chu = reg.get("楚夜")
    assert chu.status == CharacterStatus.RESURRECTED and chu.can_act
    assert chu.knowledge_limit(35) == 20  # 死后发生的事仍不知道

    # 别名合并：不产生第二张卡
    reg.create("楚公子", chapter=2)
    merged = reg.merge("楚夜", "楚公子")
    assert merged and "楚公子" in merged.aliases
    assert len([c for c in reg.all() if "楚" in c.name]) == 1


def test_hundred_characters_do_not_mean_hundred_calls(tmp_path: Path):
    reg = CharacterRegistry(tmp_path)
    for i in range(100):
        reg.create(f"群演{i}", chapter=1)
    for name in ("主角", "女主", "反派"):
        reg.create(name, chapter=1, tier=CharacterTier.CORE, auto_sim=True)
    assert len(reg.all()) == 103
    assert len(reg.simulatable()) == 3  # 只有显式 auto_sim 的才进推演


# ── 视图隔离 ──

@pytest.fixture
def world(tmp_path: Path):
    reg = CharacterRegistry(tmp_path)
    lin = reg.create("林牧", chapter=1, tier=CharacterTier.CORE, auto_sim=True)
    chu = reg.create("楚夜", chapter=1, tier=CharacterTier.CORE, auto_sim=True)
    canon = CanonLog(tmp_path)
    secret = canon.append(seed_event(
        1, "密室", "楚夜", "楚夜在密室觉醒", ["楚夜"],
        hidden="我是魔尊转世", visibility="private",
    ))
    public = canon.append(seed_event(
        1, "药圃", "楚夜", "楚夜从药圃离开", ["林牧", "楚夜"],
    ))
    future = canon.append(seed_event(
        9, "山门", "楚夜", "楚夜登上山门", ["林牧"],
    ))
    return tmp_path, reg, canon, secret, public, future


def test_view_never_contains_other_hidden_or_future(world):
    tmp_path, reg, canon, secret, public, future = world
    builder = CharacterViewBuilder(tmp_path, registry=reg, canon=canon)
    view = builder.build("林牧", 5)

    assert "魔尊转世" not in view.text
    assert secret.id not in view.event_ids
    assert public.id in view.event_ids
    assert future.id not in view.event_ids
    assert "大纲" not in view.text and "主角" not in view.text
    # 负向提示也不能出现（否则等于告诉角色有秘密）
    assert "你不知道" not in view.text and "秘密" not in view.text

    chu_view = builder.build("楚夜", 5)
    assert "我是魔尊转世" in chu_view.text  # 自己的真实想法可以知道
    assert "魔尊转世" not in builder.build("林牧", 5).text


def test_death_freezes_knowledge(world):
    tmp_path, reg, canon, secret, public, future = world
    reg.mark_dead("楚夜", chapter=5)
    after_death = canon.append(seed_event(
        6, "地宫", "楚夜", "楚夜复活", ["楚夜"],
    ))
    builder = CharacterViewBuilder(tmp_path, registry=reg, canon=canon)
    view = builder.build("楚夜", 10)
    assert after_death.id not in view.event_ids
    assert "地宫" not in view.text


def test_new_character_has_no_retroactive_percepts(world):
    tmp_path, reg, canon, secret, public, future = world
    newbie = reg.create("路人甲", chapter=5)
    old = canon.append(seed_event(
        2, "药圃", "楚夜", "楚夜杀了守卫", ["路人甲"],
        visibility="private",
    ))  # 即使 known_by 误填，入场前也不该看到
    builder = CharacterViewBuilder(tmp_path, registry=reg, canon=canon)
    view = builder.build(newbie, 6)
    assert old.id not in view.event_ids
    assert "杀了守卫" not in view.text


def test_validate_knowledge_used_rejects_outside_event(world):
    tmp_path, reg, canon, secret, public, future = world
    builder = CharacterViewBuilder(tmp_path, registry=reg, canon=canon)
    view = builder.build("林牧", 5)

    ok, _ = validate_knowledge_used(
        {"action": "离开", "knowledge_used": [public.id]}, view
    )
    assert ok
    ok, reason = validate_knowledge_used(
        {"action": "去密室", "knowledge_used": [secret.id]}, view
    )
    assert not ok and "视图外" in reason


# ── POV 推演 handler ──

class FakeLLM(BaseLLM):
    def __init__(self, content: str):
        super().__init__(model="fake")
        self.content = content
        self.messages: list = []

    async def chat(self, messages, temperature=0.7, max_tokens=4096, thinking="auto", tools=None):
        self.messages = list(messages)
        return LLMResponse(content=self.content, usage={"total_tokens": 3})

    async def stream_chat(self, messages, temperature=0.7, max_tokens=4096, thinking="auto", tools=None):
        if False:  # pragma: no cover
            yield "", ""


class FakeAgent:
    title = "写手"

    def __init__(self, llm):
        self.llm = llm


class FakeRunner:
    def __init__(self, project_dir: Path, agent):
        self.project_dir = project_dir
        self.agent = agent
        self._stop = False
        self.on_step_start = None
        self.on_step_end = None
        self.project_info = {}

    def find_agent(self, skill):
        return self.agent


def _pov_project(tmp_path: Path):
    reg = CharacterRegistry(tmp_path)
    lin = reg.create("林牧", chapter=1, tier=CharacterTier.CORE, auto_sim=True)
    canon = CanonLog(tmp_path)
    canon.append(seed_event(
        1, "密室", "楚夜", "楚夜在密室觉醒", ["楚夜"],
        hidden="我是魔尊转世", visibility="private",
    ))
    canon.append(seed_event(
        1, "药圃", "楚夜", "楚夜从药圃离开", ["林牧", "楚夜"],
    ))
    return reg, lin


def test_pov_sim_writes_only_isolated_view(tmp_path: Path):
    reg, lin = _pov_project(tmp_path)
    event_id = CanonLog(tmp_path).all()[1].id
    import json
    proposal = {
        "intent": "查清真相", "action": "去药圃查看", "dialogue": "",
        "expected_effects": ["发现线索"], "knowledge_used": [event_id], "confidence": 0.7,
    }
    llm = FakeLLM(json.dumps(proposal, ensure_ascii=False))
    runner = FakeRunner(tmp_path, FakeAgent(llm))
    step = WorkflowStep(id="pov_sim", needs="角色推演")

    asyncio.run(bs._run_pov_sim(runner, step, 3))

    out = tmp_path / "sim_cache" / "pov" / lin.id / "sim_3.json"
    assert out.exists()
    data = __import__("json").loads(out.read_text(encoding="utf-8"))
    assert data["action"] == "去药圃查看"
    assert data["_character"] == lin.id

    # 发给角色的上下文里绝不能有别人的秘密
    sent = "\n".join(m.content for m in llm.messages)
    assert "魔尊转世" not in sent
    assert "楚夜从药圃离开" in sent


def test_pov_sim_rejects_unknown_knowledge(tmp_path: Path):
    _pov_project(tmp_path)
    llm = FakeLLM('{"action":"去密室","knowledge_used":["evt_fake"]}')
    runner = FakeRunner(tmp_path, FakeAgent(llm))
    asyncio.run(bs._run_pov_sim(runner, WorkflowStep(id="pov_sim", needs="角色推演"), 3))
    assert not list((tmp_path / "sim_cache" / "pov").rglob("*.json"))


def test_pov_sim_skips_dead_characters(tmp_path: Path):
    reg, lin = _pov_project(tmp_path)
    reg.mark_dead("林牧", chapter=2)
    llm = FakeLLM('{"action":"行动","knowledge_used":[]}')
    runner = FakeRunner(tmp_path, FakeAgent(llm))
    asyncio.run(bs._run_pov_sim(runner, WorkflowStep(id="pov_sim", needs="角色推演"), 3))
    assert llm.messages == []  # 没有发起任何调用
    assert not list((tmp_path / "sim_cache" / "pov").rglob("*.json"))


def test_mass_death_is_state_update_not_simulation(tmp_path: Path):
    """灭门/屠城：30 个主要配角死亡 = 30 次状态更新 + 1 次落盘，0 次 LLM。"""
    reg = CharacterRegistry(tmp_path)
    names = [f"配角{i}" for i in range(30)]
    for name in names:
        reg.create(name, chapter=1, tier=CharacterTier.RECURRING, auto_sim=True)

    saves = []
    original_save = reg.save

    def counting_save():
        saves.append(1)
        original_save()

    reg.save = counting_save  # type: ignore[assignment]
    affected = reg.mark_dead_many(names, chapter=10, cause="宗门灭门")

    assert len(affected) == 30
    assert len(saves) == 1  # 批量只落盘一次
    assert all(c.status == CharacterStatus.DEAD for c in affected)
    assert all(c.knowledge_limit(20) == 10 for c in affected)
    assert reg.simulatable() == []  # 死亡角色不进入 POV 推演

    # 死亡角色不会再触发任何 POV 调用
    llm = FakeLLM('{"action":"行动","knowledge_used":[]}')
    runner = FakeRunner(tmp_path, FakeAgent(llm))
    asyncio.run(bs._run_pov_sim(runner, WorkflowStep(id="pov_sim", needs="角色推演"), 11))
    assert llm.messages == []
