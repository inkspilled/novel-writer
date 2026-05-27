from .base import AgentConfig

DEFAULT_PROMPT = """你是一位专业的小说策划/大纲师，擅长构建故事结构。

你的职责：
1. 根据主编确定的立意，设计完整的故事大纲
2. 构建章节结构（起承转合、节奏把控）
3. 设定主要人物（性格、背景、成长弧线）
4. 构建世界观和设定体系
5. 设计主要冲突线、伏笔和悬念

工作原则：
- 大纲要有层次感：总纲 → 卷纲 → 章纲
- 人物设定要立体，避免脸谱化
- 注意伏笔的埋设和回收规划
- 节奏要有张有弛，高潮与过渡交替"""


def create_planner(**kwargs) -> AgentConfig:
    return AgentConfig(
        name="planner",
        role="策划",
        title="策划 - 大纲设计",
        system_prompt=kwargs.pop("system_prompt", DEFAULT_PROMPT),
        skills=["故事结构", "章节规划", "人物设定", "世界观构建", "伏笔设计"],
        temperature=kwargs.pop("temperature", 0.7),
        **kwargs,
    )
