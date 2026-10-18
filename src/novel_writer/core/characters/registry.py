"""角色注册表 — 角色是数据，Agent 只是临时实例。

分级（LOD）：
- core：主角/常驻，每场可 POV 推演；
- recurring：重要配角，在场才推演，离场低频 tick；
- extra：群演，不建 Agent，只作为观察者。

状态机：
- alive → dead → resurrected / ghost / missing
- 死亡时冻结 knowledge_cutoff，之后不再获得新知识（除非亡灵/复活事件）。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable

from ..logger import get_logger
from ..project_io import write_json

logger = get_logger(__name__)

CHARACTERS_FILE = "canon/characters.json"
REGISTRY_VERSION = 1


class CharacterTier(str, Enum):
    CORE = "core"
    RECURRING = "recurring"
    EXTRA = "extra"


class CharacterStatus(str, Enum):
    ALIVE = "alive"
    DEAD = "dead"
    RESURRECTED = "resurrected"
    GHOST = "ghost"
    MISSING = "missing"
    DORMANT = "dormant"


@dataclass
class CharacterRecord:
    id: str
    name: str
    aliases: list[str] = field(default_factory=list)
    tier: CharacterTier = CharacterTier.EXTRA
    status: CharacterStatus = CharacterStatus.ALIVE
    first_seen: int = 0
    last_seen: int = 0
    location: str = ""
    goals: list[str] = field(default_factory=list)
    public_profile: dict = field(default_factory=dict)
    private_profile: dict = field(default_factory=dict)
    relationships: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)
    # 是否允许自动 POV 推演（默认关闭：开角色 panel 前由用户/导演显式打开）
    auto_sim: bool = False
    knowledge_cutoff: int = 0  # 死亡/冻结时的章号；0 = 不限制
    death_chapter: int = 0
    death_cause: str = ""
    notes: str = ""

    def __post_init__(self):
        if isinstance(self.tier, str):
            self.tier = CharacterTier(self.tier)
        if isinstance(self.status, str):
            self.status = CharacterStatus(self.status)

    # ── 能力 ──

    @property
    def can_act(self) -> bool:
        return self.status in (
            CharacterStatus.ALIVE,
            CharacterStatus.RESURRECTED,
            CharacterStatus.GHOST,
        )

    @property
    def is_dead(self) -> bool:
        return self.status in (CharacterStatus.DEAD, CharacterStatus.GHOST)

    def knowledge_limit(self, current_chapter: int) -> int:
        """角色能看到的最晚章号：死亡后冻结。"""
        if self.knowledge_cutoff:
            return min(current_chapter, self.knowledge_cutoff)
        return current_chapter

    def all_names(self) -> list[str]:
        return [self.name, *self.aliases]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["tier"] = self.tier.value
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "CharacterRecord":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


def _norm(name: str) -> str:
    return (name or "").strip().lower().replace(" ", "")


class CharacterRegistry:
    """项目级角色档案 + 生命周期状态机。"""

    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)
        self.path = self.project_dir / CHARACTERS_FILE
        self._characters: list[CharacterRecord] = []
        self._loaded = False

    # ── 持久化 ──

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.path.exists():
            return
        try:
            import json
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for item in raw.get("characters", []) if isinstance(raw, dict) else []:
                try:
                    self._characters.append(CharacterRecord.from_dict(item))
                except TypeError as e:
                    logger.warning("角色记录损坏，已跳过: %s", e)
        except Exception as e:
            logger.error("角色注册表读取失败: %s", e)

    def save(self) -> None:
        write_json(self.path, {
            "version": REGISTRY_VERSION,
            "characters": [c.to_dict() for c in self._characters],
        })

    def reload(self) -> None:
        self._characters = []
        self._loaded = False
        self._load()

    # ── 查询 ──

    def all(self) -> list[CharacterRecord]:
        self._load()
        return list(self._characters)

    def get(self, name_or_id: str) -> CharacterRecord | None:
        self._load()
        key = _norm(name_or_id)
        for c in self._characters:
            if c.id == name_or_id or _norm(c.name) == key:
                return c
        for c in self._characters:
            if any(_norm(a) == key for a in c.aliases):
                return c
        return None

    def by_id(self, char_id: str) -> CharacterRecord | None:
        self._load()
        return next((c for c in self._characters if c.id == char_id), None)

    def simulatable(self, limit: int = 4) -> list[CharacterRecord]:
        """允许自动 POV 推演且能行动的角色（按档位/最近出场排序）。"""
        self._load()
        order = {CharacterTier.CORE: 0, CharacterTier.RECURRING: 1, CharacterTier.EXTRA: 2}
        candidates = [c for c in self._characters if c.auto_sim and c.can_act]
        candidates.sort(key=lambda c: (order.get(c.tier, 9), -c.last_seen, c.name))
        return candidates[:max(0, limit)]

    # ── 生命周期 ──

    def create(
        self,
        name: str,
        *,
        chapter: int = 0,
        tier: CharacterTier = CharacterTier.EXTRA,
        public_profile: dict | None = None,
        private_profile: dict | None = None,
        goals: Iterable[str] = (),
        aliases: Iterable[str] = (),
        auto_sim: bool = False,
        location: str = "",
    ) -> CharacterRecord:
        self._load()
        existing = self.get(name)
        if existing:
            return existing
        char_id = f"char_{_norm(name).replace('/', '_') or len(self._characters)}"
        rec = CharacterRecord(
            id=char_id,
            name=name.strip(),
            aliases=[a for a in aliases if a and _norm(a) != _norm(name)],
            tier=CharacterTier(tier),
            first_seen=chapter,
            last_seen=chapter,
            public_profile=public_profile or {},
            private_profile=private_profile or {},
            goals=list(goals),
            auto_sim=auto_sim,
            location=location,
        )
        self._characters.append(rec)
        self.save()
        return rec

    def ensure(self, name: str, chapter: int = 0) -> CharacterRecord:
        """新名字先建 extra 临时卡；不生成完整人设、不打开 auto_sim。"""
        return self.create(name, chapter=chapter)

    def promote(self, name_or_id: str, tier: CharacterTier) -> CharacterRecord | None:
        rec = self.get(name_or_id)
        if not rec:
            return None
        rec.tier = CharacterTier(tier)
        self.save()
        return rec

    def mark_dead(self, name_or_id: str, chapter: int, cause: str = "") -> CharacterRecord | None:
        rec = self.get(name_or_id)
        if not rec:
            return None
        affected = self.mark_dead_many([rec.name], chapter, cause=cause)
        return affected[0] if affected else rec

    def mark_dead_many(
        self, names: Iterable[str], chapter: int, cause: str = ""
    ) -> list[CharacterRecord]:
        """批量死亡：一次落盘，零 LLM 调用。

        用于“灭门/屠城/战役阵亡”这类群像事件：30 个角色的死亡是 30 次状态
        更新 + 1 个正典事件，而不是 30 次角色推演。
        """
        self._load()
        affected: list[CharacterRecord] = []
        for name in names:
            rec = self.get(name)
            if rec is None or rec.status == CharacterStatus.DEAD:
                continue
            rec.status = CharacterStatus.DEAD
            rec.death_chapter = chapter
            rec.knowledge_cutoff = chapter
            rec.death_cause = cause
            rec.auto_sim = False
            affected.append(rec)
        if affected:
            self.save()
        return affected

    def resurrect(self, name_or_id: str, chapter: int) -> CharacterRecord | None:
        rec = self.get(name_or_id)
        if not rec:
            return None
        rec.status = CharacterStatus.RESURRECTED
        # 死后发生的事他不知道：保留 knowledge_cutoff，除非之后有告知事件
        if not rec.knowledge_cutoff:
            rec.knowledge_cutoff = chapter
        self.save()
        return rec

    def remove(self, name_or_id: str) -> bool:
        """删除角色卡（正典事件保留，不做级联清史）。"""
        self._load()
        rec = self.get(name_or_id)
        if not rec:
            return False
        self._characters = [c for c in self._characters if c.id != rec.id]
        self.save()
        return True

    def merge(self, keep: str, duplicate: str) -> CharacterRecord | None:
        """合并别名/重复卡：保留 keep，把 duplicate 的别名并入后移除。"""
        self._load()
        a = self.get(keep)
        b = self.get(duplicate)
        if not a or not b or a.id == b.id:
            return a
        for alias in [b.name, *b.aliases]:
            if _norm(alias) != _norm(a.name) and alias not in a.aliases:
                a.aliases.append(alias)
        for key, val in b.relationships.items():
            a.relationships.setdefault(key, val)
        self._characters = [c for c in self._characters if c.id != b.id]
        self.save()
        return a

    def touch(self, name_or_id: str, chapter: int) -> None:
        rec = self.get(name_or_id)
        if rec:
            rec.last_seen = max(rec.last_seen, chapter)

    def seed_from_profiles(self, profiles, chapter: int = 0) -> int:
        """从人物设定解析结果建卡（按标题推断分档，不自动 POV）。"""
        added = 0
        for p in profiles:
            if self.get(p.name):
                continue
            # 只把“可公开的身份”放 public_profile；性格/背景/动机/弱点属于本人视角，
            # 避免未来做“他人眼中的你”时把秘密当公开信息带出去。
            self.create(
                p.name,
                chapter=chapter,
                public_profile={"identity": ""},
                private_profile={
                    "personality": getattr(p, "personality", ""),
                    "background": getattr(p, "background", ""),
                    "motivation": getattr(p, "motivation", ""),
                    "weakness": getattr(p, "weakness", ""),
                },
                goals=[getattr(p, "motivation", "")] if getattr(p, "motivation", "") else [],
                tier=CharacterTier(getattr(p, "tier", "extra")),
            )
            added += 1
        return added


__all__ = [
    "CharacterTier", "CharacterStatus", "CharacterRecord", "CharacterRegistry",
    "CHARACTERS_FILE",
]
