"""LLM 输出校验：schema 校验 + 修复重试 + 最终失败显式抛错（禁止静默降级）。"""

import pytest
from pydantic import BaseModel

from core.llm import LLMOutputError, LLMProvider


class _Out(BaseModel):
    answer: str


class StubProvider(LLMProvider):
    """不触网：chat_json 返回预设脚本。"""

    def __init__(self, script: list[str]):
        self._script = list(script)
        self.calls = 0

    async def chat_json(self, messages, model=None, **kwargs):  # noqa: ANN001
        self.calls += 1
        return self._script.pop(0)


async def test_valid_first_try():
    provider = StubProvider(['{"answer": "ok"}'])
    out = await provider.chat_validated([], schema=_Out)
    assert out.answer == "ok"
    assert provider.calls == 1


async def test_repair_after_invalid_json():
    provider = StubProvider(["这不是 JSON", '{"answer": "fixed"}'])
    out = await provider.chat_validated([], schema=_Out)
    assert out.answer == "fixed"
    assert provider.calls == 2


async def test_schema_violation_also_repairs():
    """合法 JSON 但缺字段同样触发修复。"""
    provider = StubProvider(['{"wrong": 1}', '{"answer": "fixed"}'])
    out = await provider.chat_validated([], schema=_Out)
    assert out.answer == "fixed"


async def test_exhausted_repairs_raise():
    provider = StubProvider(["bad1", "bad2", "bad3"])
    with pytest.raises(LLMOutputError):
        await provider.chat_validated([], schema=_Out, max_repairs=1)
    assert provider.calls == 2
