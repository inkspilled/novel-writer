from .base import AgentConfig, BaseAgent

DEFAULT_PROMPT = """你是一位资深小说主编，拥有丰富的出版行业经验。

你的职责：
1. 帮助作者确定小说的核心立意、题材、风格和目标读者
2. 分析市场需求，给出选题建议
3. 制定整体创作规划（字数、卷数、更新节奏）
4. 在整个创作过程中提供方向性指导

工作原则：
- 尊重作者的创作意图，以引导为主
- 给出具体可执行的建议，而非空泛意见
- 关注故事的核心吸引力和读者体验
- 注意商业性与文学性的平衡"""


def create_editor(**kwargs) -> BaseAgent:
    from .base import BaseAgent as _BA  # 避免循环引用的占位

    config = AgentConfig(
        name="editor",
        role="主编",
        title="主编 - 立意规划",
        system_prompt=kwargs.pop("system_prompt", DEFAULT_PROMPT),
        skills=["选题分析", "立意规划", "风格定位", "市场建议", "创作指导"],
        temperature=kwargs.pop("temperature", 0.7),
        **kwargs,
    )
    return config
