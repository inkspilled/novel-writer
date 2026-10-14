"""LLM 与 Agent 初始化"""
from __future__ import annotations

from ...core.llm import LLMClient
from ...core.agents import load_agents
from ...core.agents.base import BaseAgent, AgentConfig
from ...core.logger import get_logger

logger = get_logger(__name__)

class LlmMixin:
    def _create_llm(self, provider: dict):
        """创建 LLM 实例 — 统一走 OpenAI 兼容协议（api_key + base_url + model）。

        provider 里可选思考方言覆盖：
        thinking_dialect / thinking_off_body / thinking_on_body
        """
        model = provider.get("model", "")
        if not model:
            return None
        base_url = provider.get("base_url", "").rstrip("/")
        if not base_url:
            base_url = "https://api.openai.com/v1"
        api_key = provider.get("api_key", "")
        extra = {}
        if provider.get("thinking_dialect"):
            extra["thinking_dialect"] = provider["thinking_dialect"]
        if provider.get("thinking_off_body"):
            extra["thinking_off_body"] = provider["thinking_off_body"]
        if provider.get("thinking_on_body"):
            extra["thinking_on_body"] = provider["thinking_on_body"]
        return LLMClient(model=model, api_key=api_key, base_url=base_url, **extra)

    def _init_llm(self):
        provider = self.config.get("current_provider")
        if not provider:
            return
        self.llm = self._create_llm(provider)

    def _init_agents(self):
        self.agents.clear()

        # 从 agents.json 读取配置
        agents_cfg = load_agents()

        # 先更新按钮（不依赖 LLM）
        self.agent_panel.update_agent_buttons(agents_cfg)

        if not self.llm:
            return

        saved_models = self.config.get("saved_models", {})

        for name, info in agents_cfg.items():
            agent_model_key = info.get("model", "").strip()
            if agent_model_key:
                saved = saved_models.get(agent_model_key)
                if saved:
                    llm = self._create_llm(saved) or self.llm
                else:
                    provider = self.config.get("current_provider", {}).copy()
                    provider["model"] = agent_model_key
                    llm = self._create_llm(provider) or self.llm
            else:
                llm = self.llm

            agent_config = AgentConfig(
                name=name,
                role=info.get("title", name),
                title=info.get("title", name),
                system_prompt=info.get("system_prompt", ""),
                skills=info.get("skills", []),
                model=agent_model_key,
                temperature=info.get("temperature", 0.7),
                max_tokens=info.get("max_tokens", 4096),
            )
            self.agents[name] = BaseAgent(agent_config, llm)

