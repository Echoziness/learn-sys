"""题目生成 Agent——回答题题干与判分要点一起由 LLM 生成，服务端校验。

设计（2026-08-11 拍板，2026-08-15 上下文工程修复）：
- LLM 生成 {question, expected_keywords}——题目问什么，判分就看什么；
- **服务端校验**：每个 expected 要点的字符必须全部出现在条目 content
  **或 LLM 自己的题干**中（场景实例词如"学号"出自题干即可通过——题目措辞
  邀请实例答案，expected 只认抽象术语会造成系统性误判）；
- **失败降维契约**：学生刚答错时，下一题只针对遗漏要点出识别/理解级题，
  深度不得升维（题目深度跟随"学生状态"，而非跟随最新一轮教学——
  重教轮的 extension 论断是深水区内容，失败后禁止入题）；
- **防重考**：已出题干注入上下文，禁止换皮重考；
- 判定"测什么"的最终权威在服务端规则，LLM 只提供候选。

脚手架（2026-08-15 重做）：LLM 一次生成完整脚手架（题干 + 正确项 + 干扰项），
正确项是从本轮教学论断提炼的完整陈述句——不再是关键词堆；干扰项首项
镜像学生错误理解。服务端校验来源与互异性，失败回退确定性构造。
"""

from __future__ import annotations

import json
import re
from typing import Any, TypedDict

from pydantic import BaseModel, Field

from core.llm import LLMProvider

# 判分要点数量上限：过多则覆盖率判定过严（答对一题要全说才 60%）。
# 题目含多个操作要求时（如"前5件+降序+名称升序"）需要更多要点，放宽到 5。
MAX_EXPECTED_KEYWORDS = 5

QUESTION_PROMPT = """你是一位经验丰富的培训讲师。请为下面这个知识条目设计一道引导性的回答题，
并列出学生回答这道题时必须覆盖的要点。

【知识条目】
{entry}

【本轮教学内容】（出题深度契约：题目只能测这些已教过的内容）
{claims}

学员难度水平：{difficulty}
{retry_section}
【已出过的题】（禁止换皮重考——不得重复相同考点的相同问法，必须换角度）：
{previous_questions}

【题目设计要求】
1. 用具体、场景化的方式提问，引导学生思考概念的含义、作用与联系——
   不要用"请解释一下X"这种泛泛的问法。
2. 题目只测【本轮教学内容】中标 core 的论断；标 extension 的应用级内容
   仅在【学生状态】显示上一题已答对时才可作为出题材料。
3. 问题必须只基于本轮已教的概念——学生只需运用已教内容即可作答，
   禁止问教学内容未覆盖的深度。
4. 问题长度 30-80 字，中文，一段话。
5. 不要给出答案，只输出问题。

【判分要点要求】
1. expected_keywords：2-5 个，学生回答需覆盖的关键概念或术语。
2. **场景化题目的 expected 必须同时包含两类要点**：概念术语（如"主键"、"外键"）
   与题干场景中的关键实例词（如"学号"、"书号"）——学生答出其中任一类
   表达同义意思即算覆盖。只有术语没有实例词时，用实例作答的学生会被误判。
3. 每个要点都必须能从条目 content 原文或你自己的题干文本中找到对应内容
   （服务端按字符来源校验，编造的要点会被丢弃）。
4. 要点要贴合你的题目：题目问什么，要点就是答什么需要的。
5. **题目中的每个具体操作要求都必须对应一个要点**——比如"前5件商品"→LIMIT、
   "按销量降序"→DESC。宁多勿漏：遗漏的要点在判分时无法被检查。

严格按 JSON 输出：
{{"question": "问题文本", "expected_keywords": ["要点1", "要点2"]}}"""


class QuestionOutput(BaseModel):
    question: str = Field(description="引导性回答题题干")
    expected_keywords: list[str] = Field(
        default_factory=list, description="回答此题需覆盖的判分要点"
    )


class ScaffoldOutput(BaseModel):
    """脚手架完整输出：题干 + 正确项 + 干扰项（LLM 一次生成，服务端校验）。"""

    question: str = Field(description="脚手架选择题题干")
    correct: str = Field(description="正确选项（一句完整陈述，出自本轮教学论断）")
    distractors: list[str] = Field(
        default_factory=list, description="干扰项（2-3 个，首项为学生错误理解镜像）"
    )


class DistractorOutput(BaseModel):
    """概念辨认选择题的干扰项组（choice 题干扰项 LLM 化）。"""

    distractors: list[str] = Field(default_factory=list, description="混淆概念词组（3 个）")


class QuestionInput(TypedDict, total=False):
    entry: dict[str, Any]
    # 本轮教学论断：{text, claim_type} dict 或裸 str（兼容旧调用方）
    taught_claims: list[dict[str, Any]] | list[str]
    retry: dict[str, Any]  # 失败信号 {missed_requirements: list[str], recent_wrong_count: int}
    difficulty_level: str  # beginner / intermediate / advanced
    previous_questions: list[str]  # 已出过的题干（防重考）


def _format_claims(claims: list[dict[str, Any]] | list[str]) -> str:
    """教学论断渲染：兼容 {text, claim_type} dict 与裸 str（旧调用方）。"""
    lines: list[str] = []
    for c in claims or []:
        if isinstance(c, str):
            lines.append(f"- {c}")
        else:
            tag = c.get("claim_type", "core")
            lines.append(f"- [{tag}] {c.get('text', '')}")
    return "\n".join(lines) if lines else (
        "（未提供——若本轮无教学内容，请基于条目内容提问，深度以条目原文为上限）"
    )


def _retry_section(retry: dict[str, Any] | None) -> str:
    """失败降维契约段：学生刚答错 → 本题必须降维聚焦。"""
    if not retry:
        return ""
    missed = "；".join(retry.get("missed_requirements") or []) or "（未提供具体遗漏）"
    return (
        f"""【学生状态】刚答错（连续错 {retry.get("recent_wrong_count", 1)} 次），
上一题遗漏的要求：{missed}

本题必须降维：只针对上述遗漏中最重要的**一个**要点出题，识别或简单理解级——
学生答出该要点即算理解。禁止综合多个要点的复杂题，禁止升维。"""
    )


def validate_distractors(distractors: list[str], correct_text: str, max_items: int = 3) -> list[str]:
    """服务端校验干扰项：去重、排除与正确项相同文本、数量上限 3。

    校验通过的才可进选项——LLM 生成的干扰项与正确项撞车会被丢弃。
    """
    valid: list[str] = []
    seen: set[str] = set()
    for d in distractors or []:
        text = (d or "").strip()
        if not text or text == correct_text:
            continue
        if text in seen:
            continue
        seen.add(text)
        valid.append(text)
        if len(valid) >= max_items:
            break
    return valid


def _tokenize(text: str) -> set[str]:
    """CJK 逐字 + 拉丁词切分（与 assess._tokens 同语义，用于词重叠校验）。"""
    out: list[str] = []
    for ch in text.lower():
        if "\u4e00" <= ch <= "\u9fff":
            out.append(ch)
            out.append(" ")
        elif ch.isalnum():
            out.append(ch)
        else:
            out.append(" ")
    return {t for t in "".join(out).split() if t}


SCAFFOLD_PROMPT = """学生在上一道回答题中答得不好，请设计一道选择题脚手架，
让学生通过对比选项发现自己的理解偏差。

【知识条目】
{entry}

【本轮教学论断】（正确选项必须从这些论断提炼，禁止引入之外的概念）
{claims}

【学生答错的题】{failed_question}
【学生作答】{student_answer}

设计要求：
1. question：选择题题干——直接指向学生答错的知识点（15-60 字）。
   可以引用学生作答中的说法提问（如"关于XX，下面哪个理解是正确的？"）。
2. correct：正确选项——一句完整、自洽的陈述（8-60 字），
   内容必须从【本轮教学论断】提炼，学生读后能确认正确理解。
3. distractors：2-3 个干扰项，每项为一句完整陈述（8-60 字）：
   - 第 1 个必须镜像学生作答中的典型错误理解（改写为陈述句）；
   - 其余为该知识点的常见误解；
   - 不得与正确项意思相同，选项间不得互相重复。

严格按 JSON 输出：
{{"question": "...", "correct": "...", "distractors": ["...", "..."]}}"""


def validate_scaffold(
    output: ScaffoldOutput, entry: dict[str, Any], claims: list[Any]
) -> ScaffoldOutput | None:
    """服务端校验脚手架输出：来源可溯（词重叠）+ 结构完整。

    正确项必须与本条目/教学论断有词重叠（防完全跑题）；题干、正确项、
    干扰项的长度与互异性校验。任一硬伤返回 None（调用方回退确定性构造）。
    """
    question = (output.question or "").strip()
    correct = (output.correct or "").strip()
    if not (6 <= len(question) <= 80) or not (4 <= len(correct) <= 80):
        return None
    claim_text = " ".join(
        c if isinstance(c, str) else c.get("text", "") for c in claims or []
    )
    source_tokens = _tokenize(
        entry.get("content", "") + " " + entry.get("title", "") + " " + claim_text
    )
    correct_tokens = _tokenize(correct)
    # 词重叠防跑题：CJK 逐字切分下单字撞车常见，至少 2 个 token 重叠才认来源
    if len(correct_tokens & source_tokens) < 2:
        return None
    distractors = validate_distractors(output.distractors, correct)
    if len(distractors) < 2:
        return None
    return ScaffoldOutput(question=question, correct=correct, distractors=distractors)


async def scaffold_node(
    state: dict[str, Any],
    *,
    provider: LLMProvider,
    model: str | None = None,
) -> ScaffoldOutput | None:
    """生成完整脚手架选择题（题干 + 正确项 + 干扰项），服务端校验。

    失败返回 None，由调用方回退确定性构造（fail-closed）。
    """
    entry = state.get("entry") or {}
    claims = state.get("taught_claims", [])
    output = await provider.chat_validated(
        [
            {
                "role": "user",
                "content": SCAFFOLD_PROMPT.format(
                    entry=json.dumps(
                        {
                            "id": entry.get("id"),
                            "title": entry.get("title"),
                            "content": entry.get("content"),
                            "keywords": entry.get("keywords", []),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    claims=_format_claims(claims),
                    failed_question=state.get("failed_question", ""),
                    student_answer=state.get("student_answer", ""),
                ),
            }
        ],
        schema=ScaffoldOutput,
        model=model,
        temperature=0.2,
    )
    return validate_scaffold(output, entry, claims)


CHOICE_DISTRACTOR_PROMPT = """为知识条目的概念辨认选择题生成干扰项。

【知识条目】
{entry}

【正确选项】（本条目的核心概念词组，服务端已定）：{correct_text}

生成 3 个干扰词组（每组 3-5 个顿号分隔的概念词）：
- 面向初学者：使用与本条目同领域、容易混淆、但**不属于本条目核心**的概念词；
- 每组可混入 1-2 个本条目的词 + 1-2 个混淆概念（半真半假，提高区分度）；
- 不得与正确选项完全相同，组间不得重复。

严格按 JSON 输出：{{"distractors": ["词1、词2、词3", "...", "..."]}}"""


async def build_choice_distractors(
    provider: LLMProvider,
    entry: dict[str, Any],
    correct_text: str,
    *,
    model: str | None = None,
) -> list[str]:
    """choice 题干扰项 LLM 化：同域混淆概念组，替换跨主题关键词堆。

    失败/校验不过返回空列表，由调用方回退确定性干扰项（fail-closed）。
    """
    try:
        output = await provider.chat_validated(
            [
                {
                    "role": "user",
                    "content": CHOICE_DISTRACTOR_PROMPT.format(
                        entry=json.dumps(
                            {
                                "id": entry.get("id"),
                                "title": entry.get("title"),
                                "content": entry.get("content"),
                                "keywords": entry.get("keywords", []),
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                        correct_text=correct_text,
                    ),
                }
            ],
            schema=DistractorOutput,
            model=model,
            temperature=0.3,
        )
    except Exception:
        return []
    return validate_distractors(output.distractors, correct_text)


def validate_expected_keywords(keywords: list[str], *sources: str) -> list[str]:
    """服务端校验判分要点：字符必须全部出自 sources（条目 content ∪ 题干等）。

    场景实例词（如"学号"）出自 LLM 自己的题干即可通过——题目措辞邀请
    实例答案，expected 只认条目术语会造成系统性误判（意思对了却被判漏）。
    校验通过的才可进判分——LLM 编造的超纲要点被丢弃。
    """
    source_chars: set[str] = set()
    for s in sources:
        source_chars |= set(re.sub(r"\s+", "", s or "").lower())
    valid: list[str] = []
    for kw in keywords or []:
        text = (kw or "").strip()
        if not text:
            continue
        chars = set(re.sub(r"\s+", "", text.lower()))
        if chars and chars.issubset(source_chars):
            valid.append(text)
        if len(valid) >= MAX_EXPECTED_KEYWORDS:
            break
    return valid


async def question_node(
    state: QuestionInput,
    *,
    provider: LLMProvider,
    model: str | None = None,
) -> dict:
    """生成回答题题干 + 判分要点（服务端校验后返回）。

    state 可含 entry（知识条目）、taught_claims（本轮教学论断，深度契约）、
    retry（失败信号——触发降维聚焦）、difficulty_level、previous_questions
    （防重考）。expected 校验失败返回空列表，由调用方回退条目原始 keywords。
    """
    entry = state.get("entry")
    if entry is None:
        return {"question": "", "expected_keywords": []}
    entry_text = json.dumps(
        {
            "id": entry["id"],
            "title": entry["title"],
            "content": entry["content"],
            "keywords": entry.get("keywords", []),
        },
        ensure_ascii=False,
        indent=2,
    )
    previous = state.get("previous_questions", [])
    previous_text = (
        "\n".join(f"- {q}" for q in previous[-5:]) if previous else "（无）"
    )
    output = await provider.chat_validated(
        [
            {
                "role": "user",
                "content": QUESTION_PROMPT.format(
                    entry=entry_text,
                    claims=_format_claims(state.get("taught_claims", [])),
                    difficulty=state.get("difficulty_level", "beginner"),
                    retry_section=_retry_section(state.get("retry")),
                    previous_questions=previous_text,
                ),
            }
        ],
        schema=QuestionOutput,
        model=model,
        temperature=0.3,
    )
    valid = validate_expected_keywords(output.expected_keywords, entry.get("content", ""), output.question)
    return {"question": output.question, "expected_keywords": valid}


__all__ = [
    "QuestionOutput",
    "ScaffoldOutput",
    "DistractorOutput",
    "validate_expected_keywords",
    "validate_distractors",
    "validate_scaffold",
    "build_choice_distractors",
    "scaffold_node",
    "question_node",
]
