"""LLM Provider：所有 LLM 调用的唯一入口，走 OpenAI 兼容协议。

无模块级单例——由组合根实例化后显式注入各 agent。
LLM 输出统一经 chat_validated 做 Pydantic 校验，失败带修复提示重试一次，仍失败抛 LLMOutputError。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TypeVar, cast

import structlog
from openai import AsyncOpenAI, BadRequestError, Timeout
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel, ValidationError

logger = structlog.get_logger()

T = TypeVar("T", bound=BaseModel)

# LLM 调用超时。AsyncOpenAI 默认 600s 无限制——API 挂起时表现为"死机"。
# 连接 10s / 读取 180s：正常 generate/review 响应 15-60s，180s 足够且不会无限挂。
LLM_TIMEOUT = Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)


class LLMOutputError(Exception):
    """LLM 输出经修复重试后仍无法通过 schema 校验。禁止静默降级，显式抛出。"""


class LLMProvider:
    """OpenAI 兼容协议的统一入口。base_url / api_key / model 全部显式传入。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        extra_body: dict | None = None,
    ):
        self.model = model
        self._extra_body = extra_body
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=LLM_TIMEOUT)
        logger.info("llm_provider_ready", base_url=base_url, model=model)

    @staticmethod
    def _sanitize_text(text: str) -> str:
        """清除孤立 surrogate（U+D800-DFFF）：粘贴文本可能带入，
        json 序列化时会导致 utf-8 编码失败。替换为 U+FFFD 而非丢弃，
        保留原始长度语义。"""
        return "".join(
            ch if not 0xD800 <= ord(ch) <= 0xDFFF else "\ufffd" for ch in text
        )

    async def chat(self, messages: list[dict[str, str]], model: str | None = None, **kwargs) -> str:
        if self._extra_body is not None:
            kwargs.setdefault("extra_body", self._extra_body)
        # 全链路净化：任何文本（学生作答/条目内容/API 返回）进入请求前
        # 清除孤立 surrogate，防止编码异常中断整轮调用。
        cleaned = [
            {**m, "content": self._sanitize_text(str(m.get("content", "")))} for m in messages
        ]
        resp = await self._client.chat.completions.create(
            model=model or self.model,
            messages=cast(Iterable[ChatCompletionMessageParam], cleaned),
            **kwargs,
        )
        choice = resp.choices[0]
        # 截断检查：finish_reason=length 说明输出被 max_tokens 掐断，
        # JSON 必然残缺——直接显式报错，避免解析半个 JSON 后走无意义的重试。
        if choice.finish_reason == "length":
            raise LLMOutputError(
                "LLM 输出被 max_tokens 截断（finish_reason=length），需要增大 max_tokens 或精简输出"
            )
        content = choice.message.content
        if content is None:
            raise LLMOutputError("LLM 返回空 content")
        return content

    async def chat_json(self, messages: list[dict], model: str | None = None, **kwargs) -> str:
        """请求 JSON 输出。对端不支持 response_format 时降级为普通请求 + 去除 markdown 围栏。"""
        try:
            raw = await self.chat(messages, model=model, response_format={"type": "json_object"}, **kwargs)
        except BadRequestError as e:
            logger.warning("json_mode_unsupported_fallback", error=str(e)[:120])
            raw = await self.chat(messages, model=model, **kwargs)
        return _strip_code_fence(raw)

    async def chat_validated(
        self,
        messages: list[dict],
        schema: type[T],
        model: str | None = None,
        max_repairs: int = 1,
        **kwargs,
    ) -> T:
        """调 LLM 并把输出校验为 schema 实例。校验失败时把错误信息回喂给模型修复，最多 max_repairs 次。"""
        last_error: Exception | None = None
        for attempt in range(max_repairs + 1):
            raw = await self.chat_json(messages, model=model, **kwargs)
            try:
                return schema.model_validate_json(raw)
            except (ValidationError, ValueError) as e:
                last_error = e
                logger.warning(
                    "llm_output_invalid",
                    schema=schema.__name__,
                    attempt=attempt,
                    error=str(e)[:200],
                )
                messages = [
                    *messages,
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": (
                            f"上次输出未通过 JSON Schema 校验：{e}\n"
                            "请仅输出修正后的合法 JSON。"
                        ),
                    },
                ]
        raise LLMOutputError(f"{schema.__name__} 校验失败（已修复重试 {max_repairs} 次）: {last_error}")


def _strip_code_fence(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return text
