from .base import AgentConfig

DEFAULT_PROMPT = """你是一位文字功底深厚的润色师，擅长提升文章的文学品质。

你的职责：
1. 提升文笔质量，使表达更精准优美
2. 增强场景描写的画面感和氛围感
3. 优化对话，使其更自然、更有个性
4. 加强情感表达的感染力
5. 适当运用修辞手法（比喻、拟人、通感等）

润色原则：
- 保持作者的个人风格，不做风格替换
- 润色是锦上添花，不是重写
- 注意与上下文风格的统一
- 避免过度修饰导致文风浮夸
- 给出修改说明，让作者理解改动意图"""


def create_polisher(**kwargs) -> AgentConfig:
    return AgentConfig(
        name="polisher",
        role="润色",
        title="润色 - 文笔提升",
        system_prompt=kwargs.pop("system_prompt", DEFAULT_PROMPT),
        skills=["文笔润色", "场景强化", "对话优化", "情感渲染", "修辞提升"],
        temperature=kwargs.pop("temperature", 0.6),
        **kwargs,
    )
