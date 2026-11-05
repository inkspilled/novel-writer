"""短剧骨架回归：依附小说项目、剧本/分镜存储、CSV/JSON 导出、剧本拆场。"""
from __future__ import annotations

import json

from src.novel_writer.core.drama import DramaStore
from src.novel_writer.models.project import Project


def test_drama_attaches_to_novel_project(tmp_path):
    project = Project(title="测试小说", genre="仙侠", style="快节奏")
    project.set_project_dir(tmp_path)
    project.save()

    store = DramaStore(tmp_path)
    store.ensure({"title": project.title, "target_chapters": 12})
    assert (tmp_path / "chapters").exists()  # 小说目录照常
    assert (tmp_path / "drama" / "series.json").exists()  # 短剧是附加目录

    loaded = Project.load(tmp_path)
    assert loaded.title == "测试小说"
    assert store.load_series()["title"] == "测试小说"


def test_drama_store_ensure_and_episode_roundtrip(tmp_path):
    store = DramaStore(tmp_path)
    store.ensure({"title": "测试短剧", "genre": "仙侠", "target_chapters": 12})

    assert store.load_series()["title"] == "测试短剧"
    episodes = store.load_episodes()
    assert len(episodes) == 1
    assert episodes[0]["number"] == 1
    assert "第1场" in store.load_script(1)

    ep = store.add_episode()
    assert ep["number"] == 2
    store.save_script(2, "# 第2集\n\n## 第1场 山门 · 夜\n\n角色：走。\n")
    assert "山门" in store.load_script(2)
    store.update_episode_meta(2, goal="夺牌", hook="骨牌亮了")
    assert store.load_episodes()[1]["goal"] == "夺牌"


def test_storyboard_roundtrip_and_export(tmp_path):
    store = DramaStore(tmp_path)
    store.ensure({"title": "测试短剧", "target_chapters": 3})
    shots = [
        {
            "shot_id": "E01-S001",
            "scene_id": "SC01",
            "shot_size": "特写",
            "camera": "推近",
            "duration_sec": 2.5,
            "location": "外门废料坑",
            "time": "夜",
            "characters": "林乐天",
            "action": "掌心被铜钉划伤",
            "dialogue": "林乐天：嘶……",
            "sfx": "金属摩擦",
            "transition": "硬切",
            "status": "approved",
            "notes": "",
        }
    ]
    store.save_storyboard(1, {"shots": shots})
    loaded = store.load_storyboard(1)
    assert loaded["schema_version"] == 1
    assert loaded["shots"][0]["shot_size"] == "特写"
    assert loaded["shots"][0]["duration_sec"] == 2.5

    csv_path = store.export_storyboard_csv(1)
    raw = csv_path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM，Excel 直接打开
    text = raw.decode("utf-8-sig")
    assert "镜号" in text and "画面/动作" in text
    assert "E01-S001" in text and "特写" in text

    json_path = store.export_storyboard_json(1)
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["episode"] == 1
    assert payload["shots"][0]["action"] == "掌心被铜钉划伤"


def test_storyboard_marks_stale_when_script_changes(tmp_path):
    """剧本改了但分镜没重生成时，必须能看出「可能过期」。"""
    store = DramaStore(tmp_path)
    store.ensure({"title": "测试短剧", "target_chapters": 2})
    store.save_script(1, "# 第1集\n\n## 第1场 山门 · 夜\n\n林乐天：走。\n")
    store.save_storyboard(1, {"shots": []}, update_source=True)
    assert store.load_storyboard(1)["script_changed"] is False

    store.save_script(1, "# 第1集\n\n## 第1场 山门 · 夜\n\n林乐天：等等。\n")
    assert store.load_storyboard(1)["script_changed"] is True

    # 只改表格并保存，不应把过期标记洗掉
    store.save_storyboard(1, {"shots": []})
    assert store.load_storyboard(1)["script_changed"] is True

    # 显式按剧本重生成后才清除
    store.save_storyboard(1, {"shots": []}, update_source=True)
    assert store.load_storyboard(1)["script_changed"] is False


def test_build_bible_from_novel(tmp_path):
    planning = tmp_path / "planning"
    planning.mkdir()
    (planning / "立意.md").write_text("# 立意\n\n逆袭与代价\n", encoding="utf-8")
    (planning / "大纲.md").write_text("# 大纲\n\n三卷结构\n", encoding="utf-8")
    (planning / "人物设定.md").write_text("# 人物\n\n林乐天：隐忍\n", encoding="utf-8")
    store = DramaStore(tmp_path)
    store.ensure({"title": "骨牌", "target_chapters": 30})
    text = store.build_bible_from_novel()
    assert "逆袭与代价" in text
    assert "三卷结构" in text
    assert "改编圣经（草稿）" in store.bible_text()


def test_import_chapter_as_episode_and_extract_assets(tmp_path):
    store = DramaStore(tmp_path)
    store.ensure({"title": "骨牌", "target_chapters": 30})
    ep = store.import_chapter_as_episode(1, "骨牌", "林乐天捡到一块骨牌。")
    script = store.load_script(ep["number"])
    assert "林乐天捡到一块骨牌" in script
    assert "## 第1场" in script

    store.save_storyboard(ep["number"], {"shots": [{
        "characters": "林乐天、苏月",
        "location": "外门废料坑",
        "action": "两人对视",
    }]})
    assert store.extract_assets_from_storyboard(ep["number"]) == 3
    assets = store.load_assets()
    assert {a["name"] for a in assets} == {"林乐天", "苏月", "外门废料坑"}
    assert store.extract_assets_from_storyboard(ep["number"]) == 0  # 不重复加


def test_shots_from_script_parses_scenes(tmp_path):
    store = DramaStore(tmp_path)
    store.ensure({"title": "测试短剧", "target_chapters": 2})
    script = """# 第1集

## 第1场 外门废料坑 · 夜

林乐天蹲在废料堆边。
林乐天：这牌子怎么在发热？

## 第2场 内门石阶 · 晨

苏月：你昨晚去哪了？
（苏月盯着他的袖口。）
"""
    shots = store.shots_from_script(script, 1)
    assert len(shots) == 2
    assert shots[0]["location"] == "外门废料坑"
    assert shots[0]["time"] == "夜"
    assert "林乐天" in shots[0]["characters"]
    assert "这牌子怎么在发热" in shots[0]["dialogue"]
    assert shots[1]["scene_id"] == "SC02"
    assert shots[1]["shot_id"] == "E01-S002"
