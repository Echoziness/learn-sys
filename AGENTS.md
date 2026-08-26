# learn-sys · AGENTS.md

> 完整背景见 `docs/产品需求文档.md`（PRD v3.0）、`docs/架构设计文档.md`、`docs/开发路线图.md` 和 `docs/开发约束与工程规范.md`。本文档是对 AI 开放的最小开发边界，不包含决策理由和反面案例——只描述正确做法。
> 产品定位一句话：**个性化教学暨学习资源生产引擎**——导学会话既是个性化教学过程，也是资源的生产过程，产出三形态资源包（讲义/分阶题/实操指南）+ 学情报告。一切工作必须落在四类证据物：可视化系统 / 指标数字 / 测试数据 / 可部署源码。

## 1. 技术栈

| 层 | 锁定 |
|---|---|
| 编排 | LangGraph (Python) ≥1.0 |
| 后端 | FastAPI + SSE ≥0.115 |
| 存储 | SQLite `knowledge.db`（FTS5 全文 + sqlite-vec 向量，单文件） |
| Embedding | BGE-M3 本地（sentence-transformers） |
| LLM 入口 | `core/llm.py` → AsyncOpenAI client，由用户通过 base_url + api_key 注入 |
| 前端 | Next.js 15 App Router + TypeScript strict |
| 样式 | Tailwind CSS v4 + shadcn/ui（`@/components/ui`） |
| 前端图表 | React Flow（@xyflow/react）+ Recharts |
| 交付 | `docker compose up --build` |
| 包管理 | Python → uv · JS → pnpm |
| CLI 交互 | stdlib readline + 输入净化（scripts/cli_input.py，开发自测用，不深投入） |

## 2. 仓库结构

```
learn-sys/
├── core/                    # Python 包（禁止任何模块级副作用）
│   ├── agents/              # 每个 agent 一个文件；节点签名 (state, *, provider, model)
│   │   ├── diagnose.py      # 学情诊断（gap_ids 收敛到本体目录）
│   │   ├── generate.py      # 领域知识生成（anchor 主条目/背景条目分离）
│   │   ├── review.py        # 审核裁判（rule_check/merge_verdicts 为纯函数，可单测）
│   │   ├── feedback.py      # LLM 判分复核 + 教学评估（fail-closed 回退规则）
│   │   ├── question.py        # 回答题题干+判分要点生成（场景化提问；expected 服务端校验字符出自 content，按 entry_id 缓存）
│   │   └── distill.py         # 误区提炼（错题/脚手架原料 → 知识化误区表述；无素材短路不调 LLM）
│   ├── state.py             # AgentState + 全部 agent 间消息的 Pydantic 模型
│   ├── graph.py             # build_teach_graph 教学子图（retrieve→generate→review）依赖注入装配
│   ├── mastery.py           # 掌握度数学唯一事实源（纯函数：加权+置信度封顶+门槛+降维判定）
│   ├── plan.py              # 课程切片（纯函数：gap匹配+前置链闭包+难度过滤+拓扑排序）+ KnowledgeEntry 模型
│   ├── assess.py            # 确定性出题/判分（纯函数：掌握度驱动题型——低掌握度选择题/高掌握度回答题，fail-closed）
│   ├── answer_pipeline.py     # 作答处理管线（判分→LLM复核→掌握度→决策，CLI/Web 共用入口）
│   ├── session.py             # SessionStore：会话/事件流/轮次/资源包 DB 读写 + 事件发射（设计见架构文档 §3/§4）
│   ├── teach_loop.py          # 会话编排服务：诊断→切片→逐主题循环，CLI/Web 共用
│   ├── deliver.py             # 资源包组装：三形态 + 溯源链 + 进阶标记
│   ├── llm.py               # LLMProvider（AsyncOpenAI，显式 180s 读取超时）+ chat_validated 校验重试
│   ├── retrieval.py         # Retriever 类：FTS5(CJK逐字切分) + sqlite-vec + RRF + 覆盖度判定
│   ├── embedding.py         # BGEEncoder（仅组合根 import，加载 ~2GB 模型）
│   ├── config.py            # Settings：全项目唯一 env 读取点
│   └── logging.py           # structlog JSON 配置
├── api/                     # FastAPI（薄层，只做序列化→转发→推流；启动时常驻装配 provider/encoder/graph）
│   ├── models.py            # API schema（Pydantic）
│   ├── main.py              # app 工actory + CORS（Settings.cors_origins，env CORS_ORIGINS）
│   ├── sse.py               # SSE 帧编码（实时与回放同构）
│   └── routes/sessions.py   # 全部端点（契约见架构文档 §5；教学/出题路径 /topics/{entry}/teach|question）
├── scripts/                 # 组合根
│   ├── cli_input.py         # CLI 交互输入层（readline 行编辑 + 输入边界净化；刻意轻量，主战场在 Web）
│   ├── init_db.py           # 幂等知识库 loader + 会话表迁移（数据来自 data/seeds/，不内嵌数据）
│   ├── export_packages.py     # 资源包条目化导出（产出物 → entries.jsonl 同构条目，自检后双写：文件交付物 + exported_entries 表，可被 init_db 原样入库）
│   └── run_cli.py           # 会话 CLI（薄壳，调 core/teach_loop）
├── data/
│   ├── seeds/<domain>/entries.jsonl  # 知识条目（一等数据文件，换目录即换领域；每条必带 knowledge_type）
│   ├── seeds/profiles/*.json         # 学习者画像种子
│   └── knowledge.db         # 知识库 + FTS5 + vec + 会话/事件/资源包（单文件，由 init_db 生成）
├── tests/                   # pytest，与 evals/metrics.py 同口径
├── evals/                   # metrics.py（三指标口径 SSOT）+ profiles/（50 组）+ gen_profiles.py（可复现生成）+ run.py（并发跑批/断点续跑）
├── web/                     # Next.js 15（W2 三画面已上线）
│   ├── app/                 # /（画像表单）/ sessions（列表）/ sessions/[id]（学生面工作台）
│   │                        # / sessions/[id]/orchestration（裁判面，live/replay 双模式）
│   │                        # / sessions/[id]/report（Recharts 三图 + 资源包浏览）
│   │                        # / resources（资源库：跨会话聚合资源包与导出条目，会话/条目双筛选）
│   ├── components/          # student/ orchestration/ report/ shared/ + ui/（shadcn，禁手改）
│   └── lib/                 # api.ts（REST）/ sse.ts（POST 流解析 + GET EventSource）/ types.ts（事件+响应 SSOT）
│                            # / orchestration-reducer.ts（事件→节点状态纯函数）
└── docker-compose.yml       # api + web + db-init（三服务已验收：db-init → api healthy → web）
```

## 3. 架构

### 3.1 依赖方向

```
web/ ──(SSE)──→ api/ ──→ core/ ──→ data/
                           │
                           └──→ LLM API（外部，走 provider 抽象）
```

### 3.2 模块化单体

运行态组件：一个 Python 进程（api）+ 一个 Node 进程（web）+ 一个 SQLite 文件。数据存储仅 `data/knowledge.db`，不引入任何额外服务。

### 3.3 图编译

`build_teach_graph(settings, provider, retriever)` 在组合根（scripts/run_cli.py、api/）装配并编译一次，编译产物由组合根持有复用。运行时只调 `graph.astream()` / `graph.ainvoke()`。core/ 内任何模块禁止 import 时产生副作用（建 client、加载模型、读 env）。

会话编排（2026-07-22 拍板）：教学子图只管 retrieve → generate → review；诊断（一次）由 CLI 直接调 `diagnose_node`；课程切片（`core/plan.py`）是确定性纯函数；内循环（出题→判分→进/停/退）由交互层驱动，不进图——等学生输入不是图的职责。

### 3.4 会话流（三层模型：本体静态 / 切片推导 / 执行动态）

```text
diagnose（LLM 一次，输出 gap_ids）→ plan（确定性切片：ID 投影 + 前置链闭包 + 拓扑排序）
  → 逐主题教学子图（retrieve[anchor 锚定] → generate → review；有任意 1 条 unsupported 时 review→generate 定向打回重写被驳回论断，上限 2 轮；retry 轮错因回流进 generate）
  → question/assess（出题：题型单向推进，answer 题干由 LLM 生成，深度以本轮教学内容为上限）
  → 学生作答 → answer_pipeline（规则判分 → LLM 复核/评估 → 掌握度更新）
  → 决策：advance（下一主题）/ retry（重教：错因回流 + 题目重生成）/ regress（回前置主题降维）
  → answer 失败后下轮先出脚手架选择题（镜像学生错误理解，答对回 answer，不计入掌握度历史）
  → answer 答对但未达门槛 → 巩固模式：跳过教学直接出确认题（确定性规则，mastery 证据只由作答累积）
```

- 交流结果只约束教学执行层，永不反向修改课程本体与切片；
- 辩论轮次硬上限 3，超限走降级（仅保留已通过论断重组）。

### 3.5 Agent 上下文（state 注入规范）

唯一事实源为 `docs/产品需求文档.md` §4（含未来节点）。当前已上线节点：

| Agent | 可读 state key | 可写 state key |
|---|---|---|
| diagnose | `learner_profile`, `test_results` | `gap_ids`（收敛到本体目录）, `gaps`, `profile_summary`, `difficulty_level` |
| retrieve | `gaps`, `difficulty_level`, `anchor_entry` | `retrieved_entries`, `uncovered_gaps` |
| generate | `retrieved_entries`, `anchor_entry`, `profile_summary`, `outline`, `last_review_feedback`, `rejected_claims`（定向改写通道，非空时只重写被驳回论断）, `retry_context`, `taught_previously`（重教轮已教论断，禁止复读）, `uncovered_gaps`, `difficulty_level` | `draft`, `cited_entries`（procedure 条目须含 procedure_guide 论断） |
| review | `draft`, `cited_entries`, `review_round` | `review_history`(append), `review_round`, `last_review_feedback`, `rejected_claims`（被驳回论断清单，每轮覆写） |
| plan（纯函数） | —（不入 state，teach_loop 直接调用） | — |
| assess（纯函数） | 条目（KnowledgeEntry）+ 当前掌握度 | 题目（按掌握度选题型） |
| feedback（LLM 节点） | 题目、作答、规则判分结果 | verdict / evaluation / missed_requirements |
| question（LLM 节点） | 条目（id/title/content/keywords）+ taught_claims（带 claim_type）+ retry 信号（失败降维）+ difficulty_level + previous_questions（防重考） | 题干 + expected（服务端校验字符出自 content ∪ 题干） |
| answer_pipeline（服务函数） | 题目、作答、掌握度历史 | AnswerOutcome（判分/评估/决策/遗漏清单，CLI 与 Web 共用） |
| deliver（纯函数，W1 已上线） | draft + review_history（或 teach_delivered 事件）、教学轮历史、knowledge_type、mastery | 讲义（仅 supported）/ 分阶题归档 / 实操指南 / 进阶挑战 / 难度层级 |
| distill（LLM，导出期调用，2026-08-23） | 错答记录 + 脚手架干扰项 + 条目（导出脚本收集自 topic_rounds） | 0-2 条误区知识（bigram 同域校验；无素材短路返回空，不调 LLM） |

每个节点只读写表内 key，越界即 code review 驳回。隔离红线：review 禁止任何画像字段（含 `profile_summary`）；generate 只读 `profile_summary` 摘要，禁止 `learner_profile` 原始模型；对话日志永不进生成上下文。

### 3.6 关键 schema 约束

- 生成 Agent 输出每条论断必含 `evidence_ids`（引用知识条目 ID 的列表）与 `claim_type`：`core`（条目覆盖层，严格证据链）/ `extension`（错因扩展层，仅重教轮出现，针对学生错因的应用级讲解，允许推导与示例但不得引入条目之外的新概念）/ `procedure_guide`（实操指南步骤，仅 procedure 条目，步骤+可运行示例+检查点）；**core 内部按教学弧组织**（2026-08-23）：概念论断（定义+类比）→ 示例论断（worked example：具体表名/列名/数据值的具体化不算编造，概念与语法不得超条目）→ 要点论断（条目原文**写明的**规则/默认行为/边界换强调形式——工程实践建议类内容条目没有就是没有，写了必被打回）；
- 审核 Agent 输出每条论断的裁决 = `supported | partially_supported | unsupported`（NLI 三分类）；**裁决语义对齐赛题幻觉本义（2026-08-26）**：幻觉 = 专业知识谬误——`unsupported` 仅留给与条目矛盾/引入条目之外新概念/无法核实的编造事实（拿不准则判，从严）；条目未写明但事实正确、与条目概念一致的领域公认常识判 `partially_supported`（过度延伸，不计幻觉）；**分级标准**：core 论断必须被条目原文明确支持（示例句/常识引申两例外）；extension 与 procedure_guide 论断降为"概念一致 + 推导自洽"（防幻觉锚点不放松——evidence_ids 照常校验，只是裁决标准分级）；讲义仍只收 supported（资源侧严格不变，只是不再把无谬误内容计为谬误）；
- 所有 agent 间消息用 Pydantic BaseModel / TypedDict 定义；
- 掌握度数学只在 `core/mastery.py`（纯函数：recency-weighted + 置信度封顶 + 门槛 0.7 + 连错 2 次降维），新增教学数值必须落在此处，禁止散落各节点；
- 出题/判分只在 `core/assess.py`（fail-closed：无 expected 关键词即判错，绝不判对）；expected 永不进学生视野（只落 topic_rounds.expected_json，不进事件流）；
- 题型仅两种且与知识类型解耦：choice（选择题，识别式）与 answer（回答题，回忆式），由掌握度驱动（<0.5 选择 / ≥0.5 回答，对齐 PRD 阶梯）；**choice 为概念辨析题**（2026-08-23 重做）：LLM 一次生成三件套（题干 + 陈述句正确项 + 误解干扰项，`choice_node`），服务端 bigram 词重叠校验（正确项 ≥2、干扰项 ≥1——单字切分下"机器学习"与"数据库"撞 3 个单字，必须用二字组），失败回退确定性关键词堆构造；判分只认选项标签（贴全文不算对），正确项位置随机化；缓存语义：未作答幂等复用，**已作答（无论对错）一律重新生成**（原题重考测不出新理解）；question_id 编码轮次与题型（`q_{entry}_r{round}_{type}`），资源包按 id 去重时不同轮/题型不互相覆盖；
- 诊断必须可复现：diagnose 调用 temperature=0（同一画像两次诊断产出一致 gap_ids，切片稳定）；出题/脚手架/干扰项生成用低温度（0.2-0.3，同会话内防抖）；
- **题型单向推进**：进入 answer 深度后不因单次失误降回泛化 choice（识别题会掩盖真实理解状态）——`build_question(floor_type=...)` 强制；真正降级由"连续 2 次答错 → regress"触发；
- **脚手架选择题**：answer 失败后下轮先出脚手架——`scaffold_node` LLM 一次生成完整三件套（题干 + 正确项 + 干扰项）：正确项是从本轮教学论断提炼的**完整陈述句**（不再是关键词堆），干扰项首项**镜像学生作答中的典型错误理解**（对比发现自己的问题）；服务端校验（结构长度 + 正确项与条目/论断词重叠 ≥2 token + 干扰项互异 ≥2 个），失败回退确定性构造（题干与关键词堆选项语义对齐）；脚手架答对回 answer，**答对不计入掌握度历史**（不打断连续错降维计数），答错计一次错；
- **评估与裁决分离**：answer 题总是送 LLM 评估（覆盖率不足的作答最有教学价值，规则预筛只降级裁决不降级评估）；判定看理解、评估教行话（学生用非术语表达正确理解 → 判 correct + 评估建议规范术语，FEEDBACK_PROMPT 含校准示例）；
- **题意核对硬收口**：feedback 复核强制拆"题目要求检查单"并输出 `missed_requirements`——LLM 判 correct 但遗漏清单非空时服务端硬降级 partial（expected 关键词覆盖率可能因 expected 不全而虚高，LLM 的遗漏清单是题意核对证据，防"漏答 LIMIT 仍判对"）；
- answer 判分两级：规则预筛（覆盖率审计信号）→ 覆盖达标与否均送 LLM 复核裁决（`core/agents/feedback.py`，可识别关键词罗列/逻辑错误并输出教学评估）；LLM 复核标准已校准——衡量"意思"而非"用词"：实例/通俗说法表达同义判 correct，只有概念错误/漏答题目关键要求/答非所问才判 partial/incorrect；LLM 失败回退规则时要求关键词全覆盖（coverage=1.0）才判对；
- 回答题模板兜底（LLM 失败回退）**不得在题干剧透判分要点**——题干列 expected 等于泄题，评估有效性优先；
- 回答题题干与判分要点由 LLM 一起生成（`core/agents/question.py`，场景化提问，禁止问条目之外内容）；expected 服务端校验——字符必须全部出自条目 content **∪ LLM 自己的题干**（场景实例词如"学号"出自题干即合法——题目措辞邀请实例答案，expected 只认抽象术语会造成系统性误判），校验失败回退条目 keywords；**场景题 expected 必须同时含概念术语与题干实例词两类要点**（学生答任一类同义表达即命中规则覆盖）；**题目中每个具体操作要求（数字/方向/关键字）必须对应一个 expected 要点**（宁多勿漏，否则漏答被判对）；pending 轮落库即缓存（teach_loop.next_question 幂等复用）；
- **出题降维契约**：学生刚答错时 `retry` 信号（遗漏清单 + 连错次数）注入出题上下文——下一题只针对最重要的一个遗漏要点出识别/理解级题，**深度不得升维**（题目深度跟随学生状态而非最新教学轮——重教轮 extension 论断是深水区内容，失败后禁止入题，防"失败后题反而更难"的死亡螺旋）；`previous_questions` 注入防换皮重考；
- **裁决权归属**（2026-08-15 修订）：answer 题判分 = 规则预筛（审计信号）→ LLM 复核裁决（唯一能判"同义表达"的层）——LLM 判 correct 且题意核对无遗漏即采纳（学生用实例/通俗说法表达同义判对）；防放水三层：矛盾检测（feedback_node 内 correct+missed 非空 → 硬降 partial）、题意核对清单、后续轮兜底（题型单向推进 + advance 需多轮 mastery 门槛 + regress）；feedback LLM 失败回退规则时仍要求覆盖率 1.0 才判对（无语义证据时从严格则）；
- **出题深度契约**：回答题必须注入教学论断（`taught_claims`，取自该条目**全部轮次** teach_delivered 事件累积，带 claim_type 分层）作为出题上限——学生只需运用已教概念即可作答，禁止问教学内容未覆盖的深度（防"教得浅、考得深"）；retry 重教后 delete_pending_rounds 作废旧题重生成；
- **巩固模式**（2026-08-15 拍板）：answer 答对但未达门槛（唯一缺口是证据数量，矛盾检测保证 correct 蕴含无遗漏）→ `TopicProgress.needs_teaching=False`，驱动方跳过教学直接出确认题——确定性规则读历史，不改变 mastery 数学（证据照常由作答累积）；choice 答对仍教学（识别→回忆之间有真实教学空间，走 `advance_hint` 通道）；
- **教学信号双通道**：`retry_context`（上一轮**答错**：题目+作答+评估，extension 论断的唯一触发源）与 `advance_hint`（上一轮 choice 答对：core 论断向应用推进）分通道注入——识别通过不是错因，混通道会污染 extension 语义（choice 答对轮教学被标成错因扩展）；
- **审核回流**（graph 条件边，2026-08-26 定向打回）：有任意 1 条当前裁决 unsupported 且 review_round<2 → review→generate **定向打回**（`rejected_claims` 通道只重写被驳回论断，supported 部分原封不动——论断间相互独立，整稿重写会复现已驳回内容且浪费成本；阈值从 2 降为 1，单条漏网不再放行）；**裁决日志模型**：`review_history` 是 append-only 裁决日志，每轮只 append 本轮新裁决的论断（rewrite 轮只复审被改写论断），论断的当前裁决 = 日志中该论断最新一条（`latest_verdicts`，闸门/事件/指标同源）——裁决属于论断不属于轮，未改写论断裁决天然不漂移（LLM 裁决轮间非确定，整稿复审会翻判——p05 实测 round1 通过的 3 条在 round2 翻判，净效果驳回 2 条变 4 条，改日志模型后同画像幻觉率 6.6%→0%）；超限放行——裁决已落 review_history，幻觉率指标照常可复算。teach_delivered 事件携带按论断取最新的当前裁决（旧轮被驳回论断已由改写版覆盖）；
- **重教去重**：重教轮 `taught_previously`（此前各轮已教论断全文）注入 generate——禁止复读，重教必须给增量（错因应用/换角度深化/未覆盖细节），防每轮 60% 重复的复读机；
- LLM 输出截断防护：`core/llm.py` 检查 finish_reason=length 显式抛 `LLMOutputError`（JSON 必然残缺，不做无意义重试）；
- 知识条目必带 `knowledge_type`（`memory` 事实/定义/术语 / `concept` 概念与关系 / `procedure` 步骤技能，枚举在 `scripts/init_db.py.KnowledgeType`，DB 列 DEFAULT 'concept'），描述知识本体（影响教学方式/门槛/复习节奏），不绑定题型，core/plan.KnowledgeEntry 同持此字段（默认 concept）；
- 新增种子条目 content 控制在 50-100 字（代码片段算字符），且每个关键词去空格后的全部字符必须出现在 content 里（判分靠子串，`tests/test_seeds.py` 全量校验）；
- DB schema 变更禁止删库重建：`scripts/init_db.py` 用幂等迁移（PRAGMA table_info 查列 → 缺则 `ALTER TABLE ADD COLUMN`），保留运行时数据与 rowid 对齐（FTS/vec 外部表依赖）。
- **产出物可复用（2026-08-23 拍板）**：资源包经 `scripts/export_packages.py` 条目化导出为与知识库**同构**的 entries.jsonl（SeedEntry schema），可被 init_db 原样入库——"系统生产的资源喂回系统"的硬证明。进库的是知识本身：讲义 supported 论断直接拼接 + distill agent 从错题/脚手架原料提炼的误区知识段落；**题目和脚手架本身不进库**（它们是提炼原料不是知识）。keywords 过滤到 content 实际命中字符（天然过种子校验）；id 规范 `GEN-{entry_id}-{learner_id}`；prerequisites/difficulty/knowledge_type 继承源条目；source 改写为生成溯源链（含审核通过率 n/m）。导出自检：schema 校验 + keywords 字符子集 + id 唯一，失败非零退出；**自检通过后双写：文件交付物 + exported_entries 表（`GET /{id}/exports` 数据源，报告页展示条目本体），并发 `packages_exported` 审计事件**（2026-08-26）；
- **审核价值数字已在事件流里**（2026-08-23 确认）：打回重写回路天然产生对照组——`review_done` 事件的 review_round=1 裁决 = 无回路时的裸幻觉率口径，teach_delivered 最终裁决 = 有回路的交付质量。收盘时评测脚本聚合两层即可出"审核机制挽救了多少幻觉"的消融对照，**无需做消融开关**；

### 3.7 会话层（W1 已上线）

- **进度从历史推导**：reached_answer / scaffold_pending / retry_context 全部由 `topic_rounds` + `mastery_snapshots` 历史计算（`teach_loop.TopicProgress`），无内存会话态——任何进程重启后从 DB 续跑（api 无状态的前提）；
- **Web 端点补充**（W2）：`GET /api/sessions`（列表，回放入口）；`GET /{id}/stream?after_seq=`（实时 SSE 订阅：先 subscribe 再补读历史、按 seq 去重，裁判面跟随另一 tab）；`GET /{id}/replay?format=json`（带 seq 的 JSON 数组，播放器步进用）；`POST /{id}/end`（session_end 收口）；`GET /{id}/topics/{entry}/state`（needs_teaching/scaffold_pending/followup_pending/prereq_id——Web 工作台镜像 CLI 状态机与刷新恢复的依据）；`POST /{id}/topics/{entry}/followup` + `POST /{id}/topics/{entry}/followup/answer`（动态追问：判定+确认题生成 / 确认题判分）；`GET /{id}/exports`（条目化导出产物，知识库同构条目，未导出时空数组）；`POST /{id}/export`（主动触发条目化导出，含误区提炼）+ `GET /{id}/export/download`（同构 jsonl 下载，已删会话的孤儿产物亦可下载）；`DELETE /{id}?keep_packages&keep_exports`（删会话与过程数据，产物可额外保留为孤儿行）；`GET /api/resources`（跨会话聚合资源库，`?session_id&entry_id` 筛选，/resources 页面数据源）；
- **事件流一表三用**：`session_events`（seq 会话内单调 + payload 自包含）同时服务裁判面渲染、回放演示、审计日志；实时 = emit 写库 + 进程内订阅推送，回放 = 按 seq 读表，前端渲染代码复用；
- **事件协议**：session_start / diagnose_done / plan_done / topic_start / retrieve_done / generate_done / review_done / teach_delivered / question_built / answer_graded / scaffold_offered / followup_asked / followup_offered / followup_graded / topic_advance / topic_regress / package_saved / packages_exported / session_end（+error）——payload 字段见架构文档 §4；
- **教学轮生命周期**：next_question 落 pending 轮（幂等，web 刷新安全）→ handle_answer 填充 answer/grade/decision → 重教时 delete_pending_rounds 作废；
- **动态追问（侧车轮）**：追问轮 = topic_rounds 侧车轮（question_id 以 `_followup` 结尾、decision='followup'），与错题→脚手架同构（有效疑问→确认型选择题）；不写掌握度快照（澄清工具非测评），`main_rounds` 过滤后题型阶梯/降维/错因推导不受影响；新提问替换旧未作答确认题，重教时随 delete_pending_rounds 作废；已答追问轮进分阶题归档，干扰项作误区提炼原料（与脚手架同源）；导出管线在 `core/export_pipeline.py`（脚本为薄壳，Web 触发端点复用同一管线）；
- **资源包 upsert 合并**：UNIQUE(session_id, entry_id)；讲义跨轮追加合并，题目按 question_id 去重（重教重生成覆盖旧版），practice/challenge 用 COALESCE 保留旧值；
- **脚手架决策收口**：脚手架答对不写掌握度快照，决策从计数历史重算（防洗白降维计数/防虚高 advance）——落在 teach_loop.handle_answer；
- **SSE 契约**：帧 = `event: <type>\ndata: <json>\n\n`（api/sse.py 编码，实时与回放同构）；LLM 失败以 error 事件透出，不静默断流；
- **api 装配**：lifespan 内创建 provider/encoder/retriever/graph/TeachLoop 常驻（BGE-M3 ~2GB 只加载一次）；会话上下文经 `TeachLoop.rebuild_context` 从 DB 重建。

## 4. 编码约定

| 层 | 约定 |
|---|---|
| Python 文件/函数 | snake_case |
| Python 类 | PascalCase |
| TS 文件 | kebab-case |
| TS 组件 | PascalCase |
| TS 函数/变量 | camelCase |
| 日志 | structlog JSON 格式 |
| 代码检查 | ruff（pyproject.toml 配置）+ pyright，两者都过才算完成 |
| 前端数据获取 | TanStack Query（客户端）/ async RSC（服务端） |
| 前端交互 | `"use client"` 仅用于 onClick/useState/SSE 订阅，其余用 RSC |
| 前端样式 | Tailwind 的 `cn()` 合并 class，不写内联 style / CSS Module |
| shadcn 组件 | 仅从 `@/components/ui` 导入，导入前确认已安装 |
| Python 错误处理 | 自定义异常 + FastAPI exception handler，不吞异常不写 `pass` |

## 6. 已踩通的坑

| 现象 | 根因 | 解法 |
|---|---|---|
| 会话跑着"死机"（无日志无报错） | `AsyncOpenAI` 默认 timeout 600s；deepseek 偶发 HTTP 200 但 body 挂起 | `core/llm.py` 显式 `Timeout(connect=10, read=180, write=30, pool=10)`——挂起变为明确报错 |
| 模拟学生全错、永远降维 | sim 模式"答对"的答案是不含关键词的占位文本，被 `grade_answer` 判错 | 答对时给出"、".join(expected_keywords) 的答案 |
| CJK 检索/匹配失配（"聚合查询"匹配不到） | 分词没在 CJK 字符间切分，中文短语成一个整词 | `plan._tokenize` / `assess._tokens` / `retrieval.segment_cjk` 三处必须同语义逐字切分 |
| LLM 输出校验失败重试后仍抛错 | `chat_validated` 重试只喂错误信息，无修复提示 | 重试消息带 Pydantic 错误详情，仍失败显式抛 `LLMOutputError`，禁止静默降级 |
| 选择题输入 A 判错（用户实测） | 中文输入法全角字母（U+FF21）或粘贴带 BOM/零宽字符，`.upper()` 不归一化 | `assess._normalize_answer`：全角→半角 + 去零宽字符，choice 判分前归一化（含回归测试） |
| 种子关键词判分失配（如 SQL-002~005 的 keyword "SQL"） | 写条目时只检查中文关键词，英文关键词字符（如 SQL 的 q）没进 content | 关键词去空格后全部字符必须出现在 content（英文词同样校验），`tests/test_seeds.py` 全量兜底 |
| 旧库重跑 init_db 缺列崩 SQL（schema 变更后） | `CREATE TABLE IF NOT EXISTS` 不会给已存在表补列 | 幂等迁移：PRAGMA table_info 查列，缺则 `ALTER TABLE ADD COLUMN`（见 `init_db.migrate_knowledge_type`） |
| 学生作答含孤立 surrogate 导致 feedback LLM 编码失败（utf-8 codec surrogates not allowed） | 粘贴文本带入 U+D800-DFFF，json 序列化/编码崩 | 双层防护：`scripts/cli_input._sanitize`（输入边界）+ `core/llm.LLMProvider._sanitize_text`（请求与响应侧都净化，纵深防御） |
| 每次 LLM 调用 15-60s（思考模式默认开启） | `deepseek-v4-flash/pro` 的 thinking 默认 enabled——先推理后输出；且官方不建议"思考 + JSON 模式"同开（response_format=json_object），与偶发 JSON 解析失败相关 | `.env` 配 `LLM_EXTRA_BODY={"thinking": {"type": "disabled"}}`——本系统所有调用都是 JSON 输出，思考无收益纯延迟 |
| 资源包题目互相覆盖（choice 轮归档消失） | question_id 不含轮次/题型，同条目不同轮的题 id 相同，upsert 按 id 去重时旧版被覆盖 | question_id 编码 `q_{entry}_r{round}_{type}`，不同轮/题型天然隔离 |
| 最终讲义只剩重教轮薄内容（首轮好论断全丢） | `_last_teach_with_verdicts` 只读最后一次 teach_delivered——各轮论断互补（taught_previously 保证），只取最后=丢内容 | `_all_teach_with_verdicts` 全轮次累积：claim_index 全局重编号 + verdicts 同步 base 偏移 + 按文本去重 + round_by_index 记来源轮 |
| choice 题不读讲义也能答对（"哪组要点属于X"关键词归属题） | 题干模板测的是元数据识别不是理解——词面匹配即可答对，与教学脱节 | choice 重做为概念辨析题：LLM 生成题干+陈述句正确项+误解干扰项，bigram 词重叠校验防跑题，正确项随机落位 |
| 教学弧加入后幻觉率回归 7.2%（要点论断层引入工程实践建议） | "操作要点/边界"层引导 LLM 写"分号习惯/性能开销/选型建议"——条目里没有，审核正确拦截（`_ADVANCE_HINT` 同源教训复发） | 要点论断限定"条目原文写明的规则换强调形式"，GENERATE_PROMPT 加判断标准"删掉例子后概念陈述必须能在条目找到"——生成端与审核锚点第三次同源校准 |
| api 启动即 ImportError（circular import） | routes 从 api.main 导入 sse_frame，main 又 import routes——模块级互相引用 | 工具函数放独立模块（api/sse.py），main 只做工厂与装配 |
| 容器内前端连不上 API | `NEXT_PUBLIC_*` 是 Next.js 构建时内联变量，运行时 environment 不生效 | 走 build args：Dockerfile `ARG` + `ENV` 在 `pnpm build` 前，compose `build.args` 传入 |
| docker 容器内 BGE 联网探测卡启动 | sentence-transformers 默认查 Hub 更新 | 容器 `HF_HUB_OFFLINE=1`（模型随 data/ 卷挂载，全离线） |
| `docker compose build` 卡 npm/pypi 下载，报 `ENETUNREACH` | daemon 的 systemd 代理 env 只作用于 `docker pull`，不注入 BuildKit 构建容器；构建容器在独立 netns 也摸不到宿主 127.0.0.1 代理 | 组合拳：`~/.docker/config.json` 配 `proxies`（CLI 侧，BuildKit 自动注入构建步骤）+ compose `build.network: host`（容器内 127.0.0.1 直达宿主代理）；交付环境无 proxies 配置时构建走直连，不受影响 |
| 容器 runtime 内 LLM 报 `Connection error`（`Connection refused`） | Docker 29 会把 `~/.docker/config.json` 的 proxies 注入 runtime 容器——容器内 `127.0.0.1:7897` 是自身 loopback，无代理监听 | compose `environment` 显式置空代理 env（`HTTP_PROXY: ""` 等）——runtime 走 LLM 直连；build 与 runtime 的网络策略独立 |
| ghcr 镜像拉取报 `403 Forbidden`/`EOF` | 两个独立根因、症状相同：①ghcr.io 对**不存在的 repo 也回 403**（而非 not found）——镜像名写错与网络不通无法区分，排查时先核对名字；②`registry-mirrors`（daemon.json）只代理 docker.io，ghcr.io 不走加速 | 拉取前先 `docker pull` 验证；ghcr 用专属镜像站（`ghcr.m.daocloud.io`）或代理直连 |
| BuildKit 报 `variable expansion is not supported for --from` | `COPY --from=${ARG}` 不支持变量展开 | ARG 放首个 FROM 前（全局作用域）+ `FROM ${UV_IMAGE} AS uv` 独立 stage，再 `COPY --from=uv` |
| 场景题学生答实例词被判"没提到术语"（覆盖率 0%，连错放逐） | 题目措辞邀请实例答案（"用什么标识学生"→答"学号"），expected 却只认条目抽象术语（"主键"）——判分锚定术语黑话 | expected 双类要点强制（概念术语 + 题干实例词），校验源放宽到 content ∪ 题干；裁决权归 LLM 题意核对（同义表达判 correct） |
| 学生答错后题目反而更难（死亡螺旋：错→重教加深→题更深→再错→regress） | 出题深度跟随"最新教学轮"而非"学生状态"——重教轮 extension 论断（深水区）进了出题上下文，且无已出题清单（换皮重考） | retry 信号（遗漏清单+连错次数）注入出题：只针对最重要遗漏要点降维出题；extension 失败后禁入题；previous_questions 防重考 |
| 脚手架题干问"正确做法"但选项是关键词堆（语义崩坏） | 题干模板与选项格式独立拼接——LLM 干扰项生成时看不到最终题干 | LLM 一次生成完整三件套（题干+正确项+干扰项），服务端校验（词重叠+互异），失败回退时题干改为与关键词堆匹配的问法 |
| 评测聚合幻觉率虚高至 96%（实际 <10%） | teach_delivered 事件的 claim_index 是**事件内局部编号**，跨事件聚合时全局重编号但 verdicts 没同步偏移——裁决全部落空，fail-closed 记 unsupported | 聚合时 claims 与 verdicts 同用 base 偏移（`base + 局部编号`），见 evals/run.py |
| shadcn CLI 在 `pnpm dlx` 下报 ERR_PACKAGE_PATH_NOT_EXPORTED（MCP sdk 引 zod 子路径） | pnpm store 链接的 @modelcontextprotocol/sdk 与 zod v3 exports 冲突，dlx 走 store 链接 | 换 `npx -y shadcn@latest add <组件>`（npm 临时安装不踩 store 链接） |
| 前端 map 回调参数 implicit any（react-query 5.101 + recharts 3 类型全崩、tsc 却不报库错） | typescript 5.0.2 < 5.4：`NoInfer` 类型未定义，react-query 类型重载静默失效（skipLibCheck 掩盖库内报错） | `pnpm add -D typescript@^5.8`——新前端依赖若用 5.4+ 类型特性，先查 TS 版本 |
| Web 调教学端点 404（E2E 冒烟抓出） | 路由实现是 `/{id}/teach/{entry}`，架构文档 §5 契约是 `/{id}/topics/{entry}/teach`，前端按文档调 | 以架构文档为契约修后端路由；新端点必须先在文档定路径再实现 |
| 前端 Button 无 asChild（shadcn new-york 新版不带 Slot） | 组件库版本差异，asChild 是 Radix Slot 的可选能力非标配 | Link 跳转用 `<Link className={button样式}>` 或 onClick + router.push |
| choice 答对轮幻觉率超标（实测 6-25%） | `_ADVANCE_HINT` 引导 LLM 讲"常见误解/易错点/选型建议"——条目里没有这些内容，审核（正确地）判 unsupported | 推进提示限定"条目概念范围内的深化"，GENERATE_PROMPT 显式声明扩展性内容会被打回——生成端与审核锚点必须同源，不能一边引导发散一边严格拦截 |

## 7. 开发模式（AI 生成代码遵循以下流程）

1. 新文件放仓库对应目录，检查 `docs/开发约束与工程规范.md` 确认目录职责；
2. 新增 Python 类先定义 Pydantic model，新增 TS 组件先定义 interface/Zod 类型；
3. 需要新依赖时说明理由，等待确认后再安装；
4. 新功能写完即写 pytest（`pytest -v` 验证），不推迟；
5. 生成 Agent 输出做 schema 校验——`evidence_ids` 字段非空；
6. 审核 Agent 上下文做字段校验——包含 `draft` 和 `cited_entries`，其余不出现；
7. **大修改完成后回写本文件**——新增 core 模块、变更架构/约定、踩通新坑都要更新对应章节（§2 结构、§3 架构、§6 坑、§8 命令）。

## 8. 常用命令

```bash
uv sync                                          # Python 依赖
uv run python scripts/run_cli.py test1 --sim 0.8 --max-rounds 1  # 会话 CLI（--sim 模拟学生 / --max-rounds 单轮验证）
uv run uvicorn api.main:app --port 8000          # API（lifespan 常驻装配，BGE 加载约 30s）
cd web && pnpm install && pnpm dev               # 前端
uv run pytest -v                                 # 测试
uv run pyright core/ scripts/ tests/ api/        # Python 类型检查
uv run ruff check .                              # Python lint（E/F/W/I/UP/B/SIM）
cd web && pnpm typecheck && pnpm lint            # 前端类型检查
uv run python scripts/init_db.py                 # 初始化知识库 + 会话表（幂等）
uv run python scripts/export_packages.py         # 资源包条目化导出（产出物 → 同构 entries.jsonl，可被 init_db 原样入库）
uv run python evals/run.py --limit 5            # 评测小批（先验幻觉率，超标即调）
uv run python evals/run.py                       # 评测全量 50 组（并发 5，~30 分钟）
docker compose up --build                        # 交付启动
```
