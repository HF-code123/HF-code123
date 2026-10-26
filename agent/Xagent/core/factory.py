"""组合根：把配置、LLM 客户端、工具注册表和 Agent 组装起来。

前端不应该自己 new 这些对象，只需要拿到 build_agent() 返回的 Agent。
这样 core 内部实现变化时，前端代码不需要改动。
"""

from __future__ import annotations

from .agent import ChatAgent
from .config import AppConfig
from .llm import OpenAILLMClient
from .tools import ToolRegistry, build_default_registry


def build_agent(
    config: AppConfig,
    registry: ToolRegistry | None = None,
) -> ChatAgent:
    return ChatAgent(
        llm=OpenAILLMClient(config.llm),
        tools=registry or build_default_registry(config.search),
        system_prompt=config.agent.system_prompt,
        max_tool_rounds=config.agent.max_tool_rounds,
    )
