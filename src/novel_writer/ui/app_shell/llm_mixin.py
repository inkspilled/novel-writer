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

    def _service_config(self, key: str) -> dict | None:
        """解析检索服务配置（embedding / rerank）：model 为空 = 不启用；地址/Key 回落当前供应商。"""
        cfg = dict(self.config.get(key) or {})
        if not cfg.get("model"):
            return None
        provider = self.config.get("current_provider") or {}
        if not cfg.get("base_url"):
            cfg["base_url"] = provider.get("base_url", "")
        if not cfg.get("api_key"):
            cfg["api_key"] = provider.get("api_key", "")
        return cfg

    def _configure_retrieval(self):
        """把 embedding / rerank 配置注入检索层（启动、改设置、跑工作流前都调用）。"""
        from ...core.rag import configure_embedding
        from ...core.rerank import configure_rerank
        configure_embedding(self._service_config("embedding"))
        configure_rerank(self._service_config("rerank"))

    def _init_llm(self):
        provider = self.config.get("current_provider")
        if not provider:
            return
        self.llm = self._create_llm(provider)
        self._configure_retrieval()

    def _init_agents(self):
        self.agents.clear()

        # 从 agents.json 读取配置
        agents_cfg = load_agents()

        # 先更新按钮（不依赖 LLM）
        self.agent_panel.update_agent_buttons(agents_cfg)

        if not self.llm:
            return

        # 所有 Agent 共用同一个 LLM/模型：跨 Agent 换模型会让同一本书的
        # 措辞、判断标准与上下文理解不一致，长跑时更容易写崩。
        for name, info in agents_cfg.items():
            agent_config = AgentConfig(
                name=name,
                role=info.get("title", name),
                title=info.get("title", name),
                system_prompt=info.get("system_prompt", ""),
                skills=info.get("skills", []),
                temperature=info.get("temperature", 0.7),
                max_tokens=info.get("max_tokens", 4096),
            )
            self.agents[name] = BaseAgent(agent_config, self.llm)

