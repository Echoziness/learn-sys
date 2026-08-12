"""LLM 流式调用（chat_stream）：逐 token 产出、surrogate 净化、截断显式报错。

Fake client 模拟 OpenAI 流式 chunk，验证收集逻辑不触网。
"""

from typing import Any, cast

import pytest

from core.llm import LLMOutputError, LLMProvider


class _FakeDelta:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, delta=None, finish_reason=None):
        self.delta = delta
        self.finish_reason = finish_reason


class _FakeChunk:
    def __init__(self, choices):
        self.choices = choices


class _FakeCompletions:
    def __init__(self, chunks):
        self._chunks = chunks
        self.kwargs: dict = {}

    async def create(self, **kwargs):
        self.kwargs = kwargs

        async def gen():
            for c in self._chunks:
                yield c

        return gen()


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeClient:
    def __init__(self, chunks):
        self.chat = _FakeChat(_FakeCompletions(chunks))


def _provider(chunks) -> tuple[LLMProvider, _FakeCompletions]:
    p = LLMProvider(base_url="https://x", api_key="k", model="m")
    client = cast(Any, p)
    client._client = _FakeClient(chunks)  # noqa: SLF001
    return p, client._client.chat.completions  # noqa: SLF001


async def test_chat_stream_collects_deltas():
    chunks = [
        _FakeChunk([_FakeChoice(delta=_FakeDelta("{"))]),
        _FakeChunk([_FakeChoice(delta=_FakeDelta("abc"))]),
        _FakeChunk([_FakeChoice(delta=_FakeDelta("}"))]),
    ]
    provider, completions = _provider(chunks)
    text = "".join([s async for s in provider.chat_stream([{"role": "user", "content": "hi"}])])
    assert text == "{abc}"
    assert completions.kwargs["stream"] is True


async def test_chat_stream_skips_empty_choices():
    chunks = [
        _FakeChunk([]),
        _FakeChunk([_FakeChoice(delta=_FakeDelta("ok"))]),
    ]
    provider, _ = _provider(chunks)
    text = "".join([s async for s in provider.chat_stream([{"role": "user", "content": "hi"}])])
    assert text == "ok"


async def test_chat_stream_truncation_raises():
    chunks = [
        _FakeChunk([_FakeChoice(delta=_FakeDelta("{"))]),
        _FakeChunk([_FakeChoice(delta=None, finish_reason="length")]),
    ]
    provider, _ = _provider(chunks)
    with pytest.raises(LLMOutputError, match="截断"):
        _ = [s async for s in provider.chat_stream([{"role": "user", "content": "hi"}])]


async def test_chat_stream_sanitizes_surrogates():
    chunks = [_FakeChunk([_FakeChoice(delta=_FakeDelta("\ud800坏"))])]
    provider, _ = _provider(chunks)
    text = "".join([s async for s in provider.chat_stream([{"role": "user", "content": "hi"}])])
    assert "\ud800" not in text
    assert "\ufffd坏" in text


async def test_chat_stream_passes_extra_body():
    chunks = [_FakeChunk([_FakeChoice(delta=_FakeDelta("x"))])]
    p = LLMProvider(
        base_url="https://x", api_key="k", model="m", extra_body={"thinking": {"type": "disabled"}}
    )
    client = cast(Any, p)
    client._client = _FakeClient(chunks)  # noqa: SLF001
    completions = client._client.chat.completions  # noqa: SLF001
    _ = [s async for s in p.chat_stream([{"role": "user", "content": "hi"}])]
    assert completions.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
