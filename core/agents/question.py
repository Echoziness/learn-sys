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
from dataclasses import dataclass
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


class ChoiceQuestionOutput(BaseModel):
    """概念辨析选择题完整输出：题干 + 陈述句正确项 + 误解干扰项。

    取代旧的"哪组要点属于X"关键词归属题——那种题测的是出席记录不是理解：
    不读讲义也能按词面匹配答对。概念辨析题要求学生真的分得清对错说法。
    """

    question: str = Field(description="概念辨析选择题题干")
    correct: str = Field(description="正确选项（一句完整、与条目一致的陈述）")
    distractors: list[str] = Field(
        default_factory=list, description="干扰项（3 个，典型误解的完整陈述句）"
    )


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


def _bigram_overlap(text: str, source: str) -> int:
    """CJK 二字组 + 拉丁词的重叠计数（跑题检测用）。

    逐字单字切分下常用字撞车严重（"机器学习标注数据"与"数据库标识"共享
    数/据/标三个单字）——二字组对齐中文词汇粒度，跑题内容几乎不可能撞出
    2 个词。拉丁词（SQL、pandas）整词参与。
    """
    def seq(s: str) -> list[str]:
        items: list[str] = []
        for ch in s.lower():
            if "\u4e00" <= ch <= "\u9fff":
                items.append(ch)
            elif ch.isalnum():
                if items and len(items[-1]) > 1 and items[-1].isascii():
                    items[-1] += ch
                else:
                    items.append(ch)
            else:
                items.append("\0")
        return items

    def bigrams(s: str) -> set[str]:
        items = seq(s)
        out: set[str] = set()
        for a, b in zip(items, items[1:], strict=False):
            if a == "\0" or b == "\0":
                continue
            if len(a) == 1 and len(b) == 1:
                out.add(a + b)  # CJK 二字组
            else:
                out.add(a if len(a) > 1 else b)  # 拉丁整词
        return out

    return len(bigrams(text) & bigrams(source))


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


FOLLOWUP_PROMPT = """学生在学习过程中主动提出了一个疑问。请先判断它是否是与本主题相关的
真实学习疑问，如果是，再为这个疑问点设计一道确认型选择题，让学生通过选项确认自己
真的理解了澄清后的内容（与错题脚手架同构：澄清工具，不是测评）。

【知识条目】
{entry}

【本轮教学论断】（澄清与正确选项的内容边界，不得引入之外的新概念）
{claims}

【学生的提问】{student_question}

判断标准（is_valid）：
- true：疑问与本条目概念或本轮教学内容相关，是一个真实的知识性疑问；
- false：寒暄/闲聊、与本主题完全无关、空泛到无法回应（如"太难了怎么办"）、
  或只是复述题干没有疑问。拿不准时判 false（fail-closed）。

is_valid=true 时同时生成三件套：
1. question：确认型选择题题干——直接针对学生的疑问点提问（15-60 字）。
2. correct：正确选项——一句完整、自洽的陈述（8-60 字），基于条目与教学论断
   正面回应学生的疑问，学生读后能确认正确理解。
3. distractors：2-3 个干扰项，每项为一句完整陈述（8-60 字）：
   - 围绕该疑问点的常见误解（不得与正确项意思相同，选项间不得互相重复）。

严格按 JSON 输出：
{{"is_valid": true, "reason": "判断依据（一句话）",
 "question": "...", "correct": "...", "distractors": ["...", "..."]}}
is_valid=false 时后三个字段留空字符串/空数组。"""


class FollowupOutput(BaseModel):
    """追问一次调用输出：有效性判定 + 确认题三件套（valid 时填充）。"""

    is_valid: bool = Field(description="是否为与本主题相关的真实学习疑问")
    reason: str = Field(default="", description="判断依据（一句话）")
    question: str = Field(default="", description="确认型选择题题干")
    correct: str = Field(default="", description="正确选项（正面回应疑问的完整陈述）")
    distractors: list[str] = Field(
        default_factory=list, description="干扰项（该疑问点的常见误解）"
    )


@dataclass(frozen=True)
class FollowupJudgement:
    """追问判定结果：无效即终止；有效时携带校验通过的脚手架同构三件套。"""

    valid: bool
    reason: str
    scaffold: ScaffoldOutput | None = None


async def followup_node(
    state: dict[str, Any],
    *,
    provider: LLMProvider,
    model: str | None = None,
) -> FollowupJudgement:
    """动态追问判定 + 生成：LLM 一次调用判定疑问有效性，有效则生成确认题三件套。

    与错题脚手架同构（复用 validate_scaffold 的来源可溯校验）。fail-closed：
    LLM 异常或三件套校验不过 → 判无效（学生收到判定理由，不进澄清管线）。
    """
    entry = state.get("entry") or {}
    claims = state.get("taught_claims", [])
    try:
        output = await provider.chat_validated(
            [
                {
                    "role": "user",
                    "content": FOLLOWUP_PROMPT.format(
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
                        student_question=state.get("student_question", ""),
                    ),
                }
            ],
            schema=FollowupOutput,
            model=model,
            temperature=0.2,
        )
    except Exception:
        return FollowupJudgement(False, "追问判定失败，请换个问法再试", None)
    if not output.is_valid:
        return FollowupJudgement(False, output.reason.strip() or "与当前学习内容无关", None)
    scaffold = validate_scaffold(
        ScaffoldOutput(
            question=output.question, correct=output.correct, distractors=output.distractors
        ),
        entry,
        claims,
    )
    if scaffold is None:
        return FollowupJudgement(False, "追问判定失败，请换个问法再试", None)
    return FollowupJudgement(True, output.reason.strip(), scaffold)


CHOICE_PROMPT = """你是培训讲师，刚给学生讲完一个知识点，现在出一道概念辨析选择题
检验学生是否真的理解了（而不是记住了几个词）。

【知识条目】
{entry}

【本轮教学论断】（正确项必须从这些论断/条目内容提炼，禁止引入之外的概念）
{claims}

学员难度水平：{difficulty}
{previous_section}
【出题要求】
1. question：概念辨析题干——直接考查对概念的理解或运用（15-60 字），如
   "关于主键的作用，下列说法正确的是？"、"小明想去除查询结果中的重复行，
   他应该怎么做？"。禁止问"哪组要点属于X"这类关键词归属题——那是元数据
   识别，测不出理解。
2. correct：正确选项——一句完整、自洽的陈述（10-60 字），内容忠于条目与
   教学论断，学生选它即证明理解了概念本身。
3. distractors：3 个干扰项，每项为一句完整陈述（10-60 字）：
   - 是该概念的**典型误解**（初学者真会犯的错，不是荒谬选项）；
   - 与正确项讨论同一个概念，但表述错误（作用说反、条件说错、概念混淆）；
   - 不得与正确项意思相同，选项间不得互相重复。

严格按 JSON 输出：
{{"question": "...", "correct": "...", "distractors": ["...", "...", "..."]}}"""


async def choice_node(
    state: dict[str, Any],
    *,
    provider: LLMProvider,
    model: str | None = None,
) -> ChoiceQuestionOutput | None:
    """生成概念辨析选择题（题干 + 陈述句正确项 + 误解干扰项），服务端校验。

    失败返回 None，由调用方回退确定性构造（fail-closed）。
    """
    entry = state.get("entry") or {}
    claims = state.get("taught_claims", [])
    previous = state.get("previous_questions", [])
    previous_section = (
        "【已出过的题】（禁止原题重考，必须换概念换角度）：\n"
        + "\n".join(f"- {q}" for q in previous[-5:])
        if previous
        else ""
    )
    try:
        output = await provider.chat_validated(
            [
                {
                    "role": "user",
                    "content": CHOICE_PROMPT.format(
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
                        difficulty=state.get("difficulty_level", "beginner"),
                        previous_section=previous_section,
                    ),
                }
            ],
            schema=ChoiceQuestionOutput,
            model=model,
            temperature=0.3,
        )
    except Exception:
        return None
    return validate_choice_question(output, entry, claims)


def validate_choice_question(
    output: ChoiceQuestionOutput, entry: dict[str, Any], claims: list[Any]
) -> ChoiceQuestionOutput | None:
    """服务端校验概念辨析选择题：来源可溯（bigram 重叠）+ 结构完整 + 互异。

    正确项须与条目/教学论断有 ≥2 个词级重叠（防跑题）；干扰项须 ≥1 个
    （误解项必须仍在讨论同一概念，但只提单个术语也算同域）；长度与互异
    校验。任一硬伤返回 None（调用方回退确定性构造）。
    """
    question = (output.question or "").strip()
    correct = (output.correct or "").strip()
    if not (10 <= len(question) <= 80) or not (6 <= len(correct) <= 80):
        return None
    claim_text = " ".join(
        c if isinstance(c, str) else c.get("text", "") for c in claims or []
    )
    source = entry.get("content", "") + " " + entry.get("title", "") + " " + claim_text
    if _bigram_overlap(correct, source) < 2:
        return None
    distractors = validate_distractors(output.distractors, correct)
    if len(distractors) < 2:
        return None
    for d in distractors:
        if not (6 <= len(d) <= 80):
            return None
        if _bigram_overlap(d, source) < 1:
            return None
    return ChoiceQuestionOutput(question=question, correct=correct, distractors=distractors)


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
    "ChoiceQuestionOutput",
    "FollowupJudgement",
    "FollowupOutput",
    "QuestionOutput",
    "ScaffoldOutput",
    "validate_expected_keywords",
    "validate_distractors",
    "validate_scaffold",
    "validate_choice_question",
    "choice_node",
    "followup_node",
    "scaffold_node",
    "question_node",
]
