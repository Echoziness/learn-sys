"""LLM Provider 抽象层。所有 LLM 调用通过此模块，走 OpenAI 兼容协议。

每个 Agent 可通过环境变量 DIAGNOSE_MODEL / GENERATE_MODEL / REVIEW_MODEL 指定独立模型。
若未设置，fallback 到全局 LLM_MODEL。不硬编码任何模型名。"""

import os, structlog
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()
logger = structlog.get_logger()


class LLMProvider:
    """OpenAI 兼容协议的统一入口。"""

    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None):
        self.base_url = base_url or os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
        self.api_key = api_key or os.getenv("LLM_API_KEY", "")
        self.model = model or os.getenv("LLM_MODEL", "gpt-4o-mini")
        self.client = AsyncOpenAI(base_url=self.base_url, api_key=self.api_key)
        logger.info("llm_provider_ready", base_url=self.base_url[:40], model=self.model)

    async def chat(self, messages: list[dict], model: str | None = None, **kwargs) -> str:
        kwargs.setdefault("extra_body", {"thinking": {"type": "disabled"}})
        resp = await self.client.chat.completions.create(
            model=model or self.model, messages=messages, **kwargs
        )
        return resp.choices[0].message.content

    async def chat_json(self, messages: list[dict], model: str | None = None, **kwargs) -> str:
        """调用 LLM 并确保返回纯 JSON 字符串。先尝试 response_format，失败则清洗。"""
        kwargs.setdefault("extra_body", {"thinking": {"type": "disabled"}})
        kwargs.setdefault("response_format", {"type": "json_object"})
        resp = await self.client.chat.completions.create(
            model=model or self.model, messages=messages, **kwargs
        )
        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        return raw


provider = LLMProvider()


def resolve_model(env_name: str) -> str | None:
    """读取环境变量指定的模型名。未设置返回 None，则使用全局 LLM_MODEL。"""
    return os.getenv(env_name) or None
