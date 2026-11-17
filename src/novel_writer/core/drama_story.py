"""故事阶段：五个标准、片段式/连贯式、配歌卡点、以及模型易犯错的对照表。

文章 L71-L118 的两段提示词（写故事 / 冷读审核）在这里落成可调用函数，
L232-L274 的 20 行「模型容易犯的错 → 应该怎么写」落成 `MODEL_PITFALLS` +
`audit_prompt_pitfalls()`，供分镜、首尾帧、视频提示词三处共用。

要点（文章原文口径）：

- 故事结构首选**片段式**（镜头之间不用接戏，最稳）；连贯式最容易穿帮。
  本项目是把长篇小说改成连续短剧，分集必须接戏，所以「连贯式」是默认；
  但单集内部仍可按片段式组织，`structure_note()` 会把差异说清楚。
- 五个标准：现实逻辑 / 一句话说清 / 开头抓人 / 旁人反应分先后 /
  AI 拍得出来。
- 配歌：转折对副歌，关键动作对重拍；片中时间按小节算。
"""
from __future__ import annotations

import re
from typing import Any

# ── 五个故事标准（文章 L74-L88）──
STORY_STANDARDS: tuple[str, ...] = (
    "符合现实逻辑：每个转折都按现实追问一遍——为什么会这样？东西从哪来？够得着吗？现实里真会这样吗？",
    "能用一句话说清想表达什么：这句话写不出来，说明故事还要改。",
    "开头就要抓人：最抓眼球的画面放在前面，写清楚第几秒出现什么。",
    "旁人的反应要像真人：每个包袱或转折分 3 段写——问题 → 主角怎么解决 → 旁人什么反应；反应要有先后，不能几个人同一秒做同一个动作。",
    "要让 AI 拍得出来：避开人物交叉、手拿小东西、文字、镜子和倒影、左右不对称的特征、一个镜头里换光、一个人连做三个动作、俯拍看不到脸、配角太多。",
)

# AI 视频的固定弱点（文章 L87，分镜/故事都要避开）
AI_WEAK_SHOTS: tuple[str, ...] = (
    "人物交叉",
    "手拿小东西",
    "文字",
    "镜子和倒影",
    "左右不对称的特征",
    "一个镜头里换光",
    "一个人连做三个动作",
    "俯拍看不到脸",
    "配角太多",
)

STORY_STRUCTURES: dict[str, str] = {
    "片段式": "每个镜头之间都不用接戏，最稳；适合原创短片。",
    "连贯式": "镜头之间要接戏，最容易穿帮；长篇改编剧集必须用，只能靠首尾帧和方向写法兜。",
}

# 文章 L232-L274：模型容易犯的错 → 应该怎么写
# `detect` 是本地体检用的识别码（None 表示只能人工核对）
MODEL_PITFALLS: tuple[dict[str, Any], ...] = (
    {
        "pitfall": "一群人同时做同一个动作",
        "fix": "写清楚谁先谁后：「老爸第一个……过一两秒老奶奶才……小女孩最后……」，首帧也不要摆同一个姿势",
        "detect": "simultaneous",
    },
    {
        "pitfall": "本来该风做的事，人自己动手做了（比如邮差用手掀开邮包）",
        "fix": "写清楚原因，再写上「两只手从头到尾都没有碰邮包」",
        "detect": "hands_on",
    },
    {
        "pitfall": "云、月亮、植物长出眼睛和嘴巴",
        "fix": "不要写「卡通云」，要写「蓬松的灰白色积云，只有棉絮一样的纹理和阴影」，动作也只写它怎么移动",
        "detect": "cartoon_face",
    },
    {
        "pitfall": "人往反方向走",
        "fix": "方向要按画面来写：「朝画面左边走，跨过左边的店门」",
        "detect": "direction",
    },
    {
        "pitfall": "一句话有两种意思（写「窗户后面的老人」，结果车里冒出人）",
        "fix": "写得更具体：「左上角石头房子的窗口里」，再加上「驾驶座一直空着」",
        "detect": "ambiguous",
    },
    {
        "pitfall": "东西的数量变了",
        "fix": "把数量直接写出来：「只有一封黄信封，之后他怀里全是白信封」",
        "detect": "quantity",
    },
    {
        "pitfall": "写「不要怎么样」经常没用（写了「壶嘴不出水」还是出水）",
        "fix": "尽量改成正面的说法：「画面里一直只有首帧这几个人」",
        "detect": "negation",
    },
    {
        "pitfall": "模型会自己联想道具的用法（壶总是出水）",
        "fix": "干脆不让这个道具出现在有动作的镜头里",
        "detect": "prop_habit",
    },
    {
        "pitfall": "东西凭空出现",
        "fix": "只用首帧里已经有的东西",
        "detect": "appear",
    },
    {
        "pitfall": "东西突然飞走或者瞬间移动",
        "fix": "写清楚它是怎么动的：「顺着后脑勺往后一滑，接住，扣回头上」",
        "detect": "teleport",
    },
    {
        "pitfall": "雨、帽子、纹身拍着拍着就没了",
        "fix": "首帧和尾帧都画上",
        "detect": "lost_props",
    },
    {
        "pitfall": "闪电画得太写实",
        "fix": "写「云里闪一下白光」，再把开头剪掉",
        "detect": "lightning",
    },
    {
        "pitfall": "一个镜头动作太多就乱了",
        "fix": "每个镜头只写一两个主要动作",
        "detect": "many_actions",
    },
    {
        "pitfall": "动作的时间和写的不一样",
        "fix": "写的时间只能算大概，多留一点时间，后期再用剪辑和变速对上节拍",
        "detect": "timing",
    },
    {
        "pitfall": "镜头推得没有写的那么明显",
        "fix": "后期让 Claude 用代码再推一点",
        "detect": "push_in",
    },
    {
        "pitfall": "人的嘴自己在动",
        "fix": "写明「人物不说话」，还动就用代码修",
        "detect": "mouth",
    },
    {
        "pitfall": "写了「没有音乐」还是混进了音乐（Seedance 2.5）",
        "fix": "片段自带的声音不用，后期另外加",
        "detect": "music",
    },
    {
        "pitfall": "结尾几帧突然跳到尾帧的样子（Seedance 2.5）",
        "fix": "后期选一张没问题的画面，后面都停在这一张",
        "detect": "end_jump",
    },
    {
        "pitfall": "第一张画面特别锐（Seedance 2.5）",
        "fix": "直接把这一张去掉",
        "detect": "sharp_first",
    },
    {
        "pitfall": "视频越到后面越暗越冷（Seedance 2.0 mini）",
        "fix": "每条视频都按它自己的首帧把颜色调回来",
        "detect": "darkening",
    },
)

_MOVE_WORDS = ("走", "跑", "冲", "退", "转身", "递", "抬", "拿", "推门", "跨过", "穿过")
_DIRECTION_WORDS = ("画面左", "画面右", "左边", "右边", "左侧", "右侧")
_SIMULTANEOUS = ("同时", "一齐", "一起", "众人", "同一秒", "所有人")
_NEGATION = ("不要", "不能", "别让", "禁止", "避免出现")
_TELEPORT = ("瞬间移动", "突然飞走", "凭空飞", "一下子飞到", "原地消失")
_APPEAR = ("凭空出现", "突然出现", "冒出来", "冒出了")
_LOST = ("雨", "帽子", "纹身", "围巾", "疤痕")
_AMBIGUOUS = ("窗户后面", "门口的人", "车上的人", "后面的人")
_PROP_HABIT = ("壶", "水壶", "茶壶", "杯子")
_QUANTITY = ("一个", "两个", "三个", "一群", "一堆", "几封", "几把")


def structure_note(structure: str) -> str:
    key = "片段式" if "片段" in (structure or "") else "连贯式"
    return STORY_STRUCTURES[key]


def parse_song(text: str) -> dict[str, Any]:
    """解析「歌名、时长、BPM」；解析不出的字段留空，不猜。

    支持：``起风了、240秒、92BPM`` / ``《起风了》 3:20 92bpm`` / ``起风了 92``。
    """
    raw = (text or "").strip()
    result: dict[str, Any] = {"name": "", "seconds": 0.0, "bpm": 0.0, "raw": raw}
    if not raw:
        return result
    bpm_match = re.search(r"(\d{2,3}(?:\.\d+)?)\s*(?:bpm|拍/分)", raw, flags=re.IGNORECASE)
    if bpm_match:
        result["bpm"] = float(bpm_match.group(1))
        raw = raw[: bpm_match.start()] + " " + raw[bpm_match.end():]
    seconds = 0.0
    clock = re.search(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", raw)
    if clock:
        seconds = int(clock.group(1)) * 60 + int(clock.group(2))
        raw = raw[: clock.start()] + " " + raw[clock.end():]
    else:
        sec = re.search(r"(\d+(?:\.\d+)?)\s*(?:秒|s|sec)", raw, flags=re.IGNORECASE)
        if sec:
            seconds = float(sec.group(1))
            raw = raw[: sec.start()] + " " + raw[sec.end():]
    result["seconds"] = round(seconds, 2)
    name = re.sub(r"[、,，;；:：\s]+", " ", raw).strip(" 《》\"'“”")
    if not result["bpm"] and not result["seconds"]:
        # 只有歌名 + 一个裸数字时，按 BPM 认（如「起风了 92」）
        bare = re.search(r"(?<!\d)(\d{2,3})(?!\d)\s*$", name)
        if bare:
            result["bpm"] = float(bare.group(1))
            name = name[: bare.start()].strip()
    result["name"] = name
    return result


def beat_interval(bpm: float) -> float:
    return 60.0 / float(bpm) if bpm and bpm > 0 else 0.0


def beat_grid(seconds: float, bpm: float, *, offset: float = 0.0) -> list[float]:
    """拍点时间表：第 0 拍在 `offset`，之后每 60/BPM 秒一个。"""
    step = beat_interval(bpm)
    if step <= 0 or seconds <= 0:
        return []
    times: list[float] = []
    t = float(offset)
    while t <= seconds + 1e-6:
        times.append(round(t, 3))
        t += step
    return times


def bar_grid(seconds: float, bpm: float, *, beats_per_bar: int = 4, offset: float = 0.0) -> list[float]:
    """小节时间表：配歌时「片中时间按小节算」，分镜表用这个对齐。"""
    step = beat_interval(bpm) * max(1, int(beats_per_bar))
    if step <= 0 or seconds <= 0:
        return []
    times: list[float] = []
    t = float(offset)
    while t <= seconds + 1e-6:
        times.append(round(t, 3))
        t += step
    return times


def bar_index_at(time_sec: float, bpm: float, *, beats_per_bar: int = 4, offset: float = 0.0) -> int:
    step = beat_interval(bpm) * max(1, int(beats_per_bar))
    if step <= 0:
        return 0
    return max(0, int((float(time_sec) - float(offset)) // step))


def bar_label(time_sec: float, bpm: float, *, beats_per_bar: int = 4, offset: float = 0.0) -> str:
    """`第 3 小节 + 2 拍` 这种写法；没配歌就退回秒数。"""
    if not bpm or bpm <= 0:
        return f"{float(time_sec):.1f} 秒"
    beat = beat_interval(bpm)
    index = bar_index_at(time_sec, bpm, beats_per_bar=beats_per_bar, offset=offset)
    in_bar = (float(time_sec) - offset - index * beat * beats_per_bar) / beat if beat else 0
    if abs(in_bar - round(in_bar)) < 0.25:
        return f"第 {index + 1} 小节第 {int(round(in_bar)) + 1} 拍"
    return f"第 {index + 1} 小节 + {in_bar:.1f} 拍"


def story_prompt(
    idea: str,
    seconds: int,
    *,
    song: str = "",
    reference: str = "",
    structure: str = "连贯式",
) -> str:
    """文章 L90-L104 的写故事提示词（片段式/连贯式按本剧实际情况填）。"""
    idea = (idea or "").strip() or "（一句话或初步剧情）"
    lines = [f"我想做的片子：{idea}。片长：{int(seconds)} 秒。"]
    if song.strip():
        lines.append(f"配歌：{song.strip()}。")
    if reference.strip():
        lines.append(f"参考片：{reference.strip()}。")
    lines += [
        "",
        "先帮我把这个想法完善成一个完整的故事。有不清楚的地方就问我，我们一起聊，等我确认了再往下写。",
    ]
    if structure == "片段式":
        lines.append("故事结构首选片段式，因为每个镜头之间都不用接戏，最稳。连贯式最容易穿帮，除非我要求，否则不要用。")
    else:
        lines.append(
            "这部是长篇改编的连续剧集，集与集之间必须接戏，所以用连贯式；"
            "但一集内部的镜头尽量按片段式组织，少接头，减少穿帮。"
        )
    lines += [
        "",
        "故事确定以后，展开成逐镜头的故事，要满足下面这些要求：",
        "1. 符合现实逻辑。每个转折都按现实追问一遍：为什么会这样？东西从哪来？够得着吗？现实里真会这样吗？",
        "2. 能用一句话说清想表达什么。先把这句话写出来，说不出来就说明故事还要改。",
        "3. 开头就要抓人。最抓眼球的画面放在前面，写清楚第几秒出现什么。",
        "4. 旁人的反应要像真人。每个包袱或者转折都分 3 段写：问题 → 主角怎么解决 → 旁人什么反应。"
        "反应要有先后，不能几个人同一秒做同一个动作。细节也要真实，比如人吃惊时身体会往后缩。",
        "5. 要让 AI 拍得出来。避开这些弱点：" + "、".join(AI_WEAK_SHOTS) + "。",
        "",
        "如果配歌，转折对副歌，关键动作对重拍。",
    ]
    return "\n".join(lines)


def story_review_prompt(story_text: str) -> str:
    """文章 L109-L117 的冷读审核提示词：只给故事，不给结论。"""
    return "\n".join([
        "这是一支短片的逐镜头故事。你是第一次看，只负责挑错，不改文件。",
        "1. 只按镜头顺序读，先写出你看懂的故事。",
        "2. 按顺序追问每个转折：为什么会这样？东西从哪来？够得着吗？现实里真会这样吗？",
        "3. 能用一句话说清想表达什么吗？",
        "4. 开头抓人吗？最抓眼球的画面放在前面了吗？",
        "5. 旁人的反应像真人吗？有没有先后？",
        "6. 哪些镜头 AI 视频模型容易拍错？（" + "、".join(AI_WEAK_SHOTS) + "）",
        '每条写：镜头、问题、建议改法。没把握的写"可能"。最后一行只写"通过"或"不通过"，不加标点。',
        "",
        "【逐镜头故事】",
        (story_text or "").strip() or "（空）",
    ])


def music_alignment_note(song: str, seconds: int, shots: list[dict[str, Any]] | None = None) -> str:
    """配歌卡点说明：转折对副歌、关键动作对重拍，并给每镜标小节。"""
    parsed = parse_song(song)
    if not parsed.get("bpm"):
        return "配歌：没测出 BPM，先按秒数排；卡点等拿到原曲测完拍点再对。"
    bpm = float(parsed["bpm"])
    lines = [
        f"配歌：{parsed['name'] or '（未命名）'} · {parsed['seconds'] or seconds:.0f} 秒 · {bpm:.0f} BPM。",
        f"- 一小节 = 4 拍 ≈ {4 * 60 / bpm:.2f} 秒；片中时间按小节标。",
        "- 转折对副歌，关键动作对重拍。",
        "- 拍点只从原曲上测，不要在剪过的文件上测。",
    ]
    for shot in shots or []:
        try:
            start = float(shot.get("start_sec") or 0)
        except (TypeError, ValueError):
            start = 0.0
        shot_id = str(shot.get("shot_id") or "")
        if shot_id:
            lines.append(f"  - {shot_id}：{bar_label(start, bpm)}")
    return "\n".join(lines)


def audit_prompt_pitfalls(text: str, *, has_keyframes: bool = False) -> list[str]:
    """按 20 行错误对照表做本地体检；能自动查的查，查不了的明确标「人工核对」。"""
    body = text or ""
    issues: list[str] = []
    # 结尾的全体定格是合法约束，不能当作多人同步动作。
    action_body = re.sub(r"最后\s*0\.3\s*秒：[^\n]*", "", body)
    if any(word in action_body for word in _SIMULTANEOUS):
        issues.append("有多人同时动作：写清谁先谁后，首帧也别摆同一姿势")
    if any(word in body for word in _NEGATION):
        issues.append("有「不要/不能」类否定句：改成正面的「画面里一直只有……」")
    if any(word in body for word in _TELEPORT):
        issues.append("有「瞬间移动/突然飞走」：写清它到底怎么动的")
    if any(word in body for word in _APPEAR) and "首帧" not in body:
        issues.append("有东西凭空出现：只用首帧里已经有的东西")
    if any(word in body for word in _MOVE_WORDS) and not any(word in body for word in _DIRECTION_WORDS):
        issues.append("人物有走动/递物但没写画面左/右：容易走反方向")
    if any(word in body for word in ("卡通云", "卡通月亮", "拟人")) or re.search(r"(云|月亮|植物).{0,6}(眼睛|嘴巴|脸)", body):
        issues.append("云/月亮/植物可能有五官：写具体材质，只写它怎么移动")
    if any(word in body for word in _PROP_HABIT) and any(word in body for word in ("倒水", "出水", "拿起", "举起")):
        issues.append("道具（壶/杯）在有动作的镜头里：模型会自己联想用法，尽量挪走")
    if any(word in body for word in _LOST) and has_keyframes and "首帧" not in body and "尾帧" not in body:
        issues.append("雨/帽子/纹身这类小物件：首帧和尾帧都要画上，只写文字会掉")
    if re.search(r"推近|推进|推镜", body) and not any(word in body for word in ("裁切", "放大", "后期")):
        issues.append("写了推镜：尾帧用同一构图裁切放大，后期再补一点，否则看不出推")
    if "人物不说话" not in body and any(word in body for word in ("对话", "说话", "开口", "嘴")):
        issues.append("有人物说话/嘴部描写：固定写「人物不说话」，还动就用代码修")
    if "没有音乐" in body:
        issues.append("写了「没有音乐」也可能混进音乐（Seedance 2.5）：片段自带声音不用，后期另加")
    if "尾帧" not in body and re.search(r"最后|结尾", body):
        issues.append("结尾没对齐尾帧：模型最后几帧会突然跳到尾帧样子")
    if body.count("然后") >= 3:
        issues.append("一个镜头里动作太多：每镜只写一两个主要动作")
    return issues


def pitfalls_markdown() -> str:
    """20 行对照表的 Markdown 版（导出的提示词文件里带一份）。"""
    lines = ["| 模型容易犯的错 | 应该怎么写 |", "|---|---|"]
    for row in MODEL_PITFALLS:
        lines.append(f"| {row['pitfall']} | {row['fix']} |")
    return "\n".join(lines)
