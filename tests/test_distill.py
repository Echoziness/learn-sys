"""distill agent 测试：校验规则 + 无素材短路 + LLM 全流程（Fake provider）。"""

from __future__ import annotations

from core.agents.distill import DistillOutput, distill_pitfalls, validate_pitfalls

ENTRY = {
    "id": "BDA-DB-001",
    "title": "关系型数据库基本概念",
    "content": "关系型数据库以表为基本存储单元，表由行和列组成。主键唯一标识一行，"
    "外键引用其他表的主键以建立表间联系。",
}


class FakeProvider:
    def __init__(self, output: DistillOutput | Exception):
        self._output = output
        self.calls = 0

    async def chat_validated(self, messages, schema, model=None, **kwargs):
        self.calls += 1
        if isinstance(self._output, Exception):
            raise self._output
        return self._output


# ---------- validate_pitfalls ----------


def test_validate_accepts_ondomain_pitfall():
    out = validate_pitfalls(
        ["常见误区：认为主键只要非空即可；正确理解是主键必须唯一标识一行。"], ENTRY
    )
    assert len(out) == 1


def test_validate_rejects_offtopic_and_bad_length():
    out = validate_pitfalls(
        [
            "太短",
            "量子纠缠是微观粒子的关联现象，与宏观世界完全不同，需要专门学习。",  # 跑题
        ],
        ENTRY,
    )
    assert out == []


def test_validate_dedup_and_cap():
    text = "常见误区：认为主键可以重复；正确理解是主键唯一标识一行。"
    out = validate_pitfalls([text, text, text], ENTRY)
    assert out == [text]  # 去重
    a = "常见误区：认为主键可以重复；正确理解是唯一标识。"
    b = "常见误区：认为外键随意引用；正确理解是引用主键。"
    c = "常见误区：认为表没有列；正确理解是表由行列组成。"
    assert len(validate_pitfalls([a, b, c], ENTRY)) == 2  # 上限 2


# ---------- distill_pitfalls ----------


async def test_distill_shortcircuits_without_material():
    provider = FakeProvider(DistillOutput(pitfalls=["不应被调用"]))
    out = await distill_pitfalls(
        {"entry": ENTRY, "wrong_records": [], "scaffold_distractors": []},
        provider=provider,  # type: ignore[arg-type]
    )
    assert out == []
    assert provider.calls == 0


async def test_distill_llm_path_validates():
    provider = FakeProvider(
        DistillOutput(
            pitfalls=[
                "常见误区：认为主键只要非空即可；正确理解是主键必须唯一标识一行。",
                "量子纠缠是微观粒子的关联现象，与宏观世界完全不同。",  # 跑题被丢
            ]
        )
    )
    out = await distill_pitfalls(
        {
            "entry": ENTRY,
            "wrong_records": [
                {"prompt": "用什么标识每行？", "answer": "主键不重复就行",
                 "evaluation": "遗漏唯一性", "missed": ""}
            ],
            "scaffold_distractors": [],
        },
        provider=provider,  # type: ignore[arg-type]
    )
    assert len(out) == 1
    assert "主键" in out[0]


async def test_distill_llm_failure_returns_empty():
    provider = FakeProvider(RuntimeError("LLM down"))
    out = await distill_pitfalls(
        {
            "entry": ENTRY,
            "wrong_records": [{"prompt": "q", "answer": "a", "evaluation": "", "missed": ""}],
            "scaffold_distractors": [],
        },
        provider=provider,  # type: ignore[arg-type]
    )
    assert out == []
