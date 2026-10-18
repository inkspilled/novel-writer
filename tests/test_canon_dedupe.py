from pathlib import Path

from src.novel_writer.core.canon.events import CanonEvent, CanonLog


def test_append_skips_exact_duplicate(tmp_path: Path):
    canon = CanonLog(tmp_path)
    ev = CanonEvent(chapter=1, actor="顾言", observable="推门", type="action")
    a = canon.append(ev)
    b = canon.append(CanonEvent(chapter=1, actor="顾言", observable="推门", type="action"))
    assert a.id == b.id
    assert len(canon.all()) == 1


def test_same_text_different_hidden_not_deduped(tmp_path: Path):
    canon = CanonLog(tmp_path)
    canon.append(CanonEvent(chapter=1, actor="顾言", observable="推门", hidden="想逃", type="action"))
    canon.append(CanonEvent(chapter=1, actor="顾言", observable="推门", hidden="想杀", type="action"))
    assert len(canon.all()) == 2
