"""人物设定标题 → 规范名/分档。"""
from pathlib import Path

from src.novel_writer.core.character_sim import parse_characters
from src.novel_writer.core.characters.naming import canonical_name, infer_tier
from src.novel_writer.core.characters.registry import CharacterRegistry, CharacterTier


def test_canonical_name_and_tier():
    assert canonical_name("顾言（主角）") == "顾言"
    assert canonical_name("宗主·沈玄微（亦师亦敌）") == "沈玄微"
    assert canonical_name("内门执事·赵九衡（卷一反派）") == "赵九衡"
    assert canonical_name("裁天傀儡·顾言（卷四幻境）") == "裁天傀儡·顾言"
    assert infer_tier("顾言（主角）") == "core"
    assert infer_tier("宗主·沈玄微（亦师亦敌）") == "core"
    assert infer_tier("内门执事·赵九衡（卷一反派）") == "recurring"
    assert infer_tier("裁天傀儡·顾言（卷四幻境）") == "extra"


def test_parse_and_seed_uses_tier(tmp_path: Path):
    doc = '''## 顾言（主角）\n动机：讨公道\n\n## 苏怀瑾（挚友/剑修）\n动机：护道\n\n## 路人甲（群演）\n'''
    profiles = parse_characters(doc)
    assert [p.name for p in profiles] == ["顾言", "苏怀瑾", "路人甲"]
    reg = CharacterRegistry(tmp_path)
    reg.seed_from_profiles(profiles, chapter=1)
    assert reg.get("顾言").tier == CharacterTier.CORE
    assert reg.get("苏怀瑾").tier == CharacterTier.RECURRING
    assert reg.get("路人甲").tier == CharacterTier.EXTRA
