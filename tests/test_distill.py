"""distill agent 测试：校验规则 + 无素材短路 + 证据锚定 + LLM 全流程（Fake provider）。"""

from __future__ import annotations

from core.agents.distill import (
    DistillOutput,
    PitfallItem,
    distill_pitfalls,
    validate_pitfalls,
)

ENTRY = {
    "id": "BDA-DB-001",
    "title": "关系型数据库基本概念",
    "content": "关系型数据库以表为基本存储单元，表由行和列组成。主键唯一标识一行，"
    "外键引用其他表的主键以建立表间联系。",
}

# 讲义锚点（知识化过滤后的论断，序号即 evidence_ids 取值）
CLAIMS = [
    {"text": "主键是表中唯一标识每一行记录的字段，取值不得重复。"},
    {"text": "外键用于引用其他表的主键，从而建立表与表之间的联系。"},
]


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


def test_validate_rejects_personal_reference():
    """含学习者指涉的误区直接丢弃（可复用知识与学习者无关）。"""
    out = validate_pitfalls(
        ["对于学机械的你而言，常见误区：认为主键可重复；正确理解是主键唯一标识一行。"],
        ENTRY,
    )
    assert out == []


def test_validate_requires_evidence_anchor_with_claims():
    """给了讲义锚点时：正确理解必须锚得上讲义（bigram 重叠），锚不上即丢。"""
    anchored = PitfallItem(
        text="常见误区：认为主键只要非空即可；正确理解是主键必须唯一标识一行。",
        evidence_ids=[0],
    )
    unanchored = PitfallItem(
        text="常见误区：认为索引能代替主键；正确理解是查询优化器自动选择索引。",  # 讲义无此内容
        evidence_ids=[1],
    )
    no_evidence = PitfallItem(
        text="常见误区：认为主键可以重复；正确理解是主键唯一标识一行。",
        evidence_ids=[],
    )
    out = validate_pitfalls([anchored, unanchored, no_evidence], ENTRY, CLAIMS)
    assert out == [anchored.text]


# ---------- distill_pitfalls ----------


async def test_distill_shortcircuits_without_material():
    provider = FakeProvider(DistillOutput(pitfalls=[PitfallItem(text="不应被调用")]))
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
                PitfallItem(
                    text="常见误区：认为主键只要非空即可；正确理解是主键必须唯一标识一行。",
                    evidence_ids=[0],
                ),
                # 跑题：锚不上讲义也被丢（量子纠缠与讲义零重叠）
                PitfallItem(
                    text="量子纠缠是微观粒子的关联现象，与宏观世界完全不同。",
                    evidence_ids=[0],
                ),
            ]
        )
    )
    out = await distill_pitfalls(
        {
            "entry": ENTRY,
            "wrong_records": [
                {"prompt": "用什么标识每行？", "answer": "主键不重复就行", "missed": ""}
            ],
            "scaffold_distractors": [],
            "taught_claims": CLAIMS,
        },
        provider=provider,  # type: ignore[arg-type]
    )
    assert len(out) == 1
    assert "主键" in out[0]


async def test_distill_prompt_excludes_evaluation_and_injects_claims():
    """原料卫生：评估文本（第二人称个性化措辞）不进上下文，讲义锚点进上下文。"""
    captured: dict[str, str] = {}

    class CaptureProvider:
        async def chat_validated(self, messages, schema, model=None, **kwargs):
            captured["content"] = messages[0]["content"]
            return DistillOutput(pitfalls=[])

    await distill_pitfalls(
        {
            "entry": ENTRY,
            "wrong_records": [
                {"prompt": "q", "answer": "a", "missed": ""}
            ],
            "scaffold_distractors": [],
            "taught_claims": CLAIMS,
        },
        provider=CaptureProvider(),  # type: ignore[arg-type]
    )
    assert "唯一标识每一行记录" in captured["content"]  # 讲义锚点在上下文里


async def test_distill_llm_failure_returns_empty():
    provider = FakeProvider(RuntimeError("LLM down"))
    out = await distill_pitfalls(
        {
            "entry": ENTRY,
            "wrong_records": [{"prompt": "q", "answer": "a", "missed": ""}],
            "scaffold_distractors": [],
        },
        provider=provider,  # type: ignore[arg-type]
    )
    assert out == []
