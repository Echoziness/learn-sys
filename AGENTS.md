# learn-sys · AGENTS.md

> 完整背景见 `docs/技术选型与架构决策.md` 和 `docs/开发约束与工程规范.md`。本文档是对 AI 开放的最小开发边界，不包含决策理由和反面案例——只描述正确做法。

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
| CLI 交互 | prompt_toolkit（scripts/cli_input.py：方向键选择题 + 行编辑回答题 + 输入边界净化） |

## 2. 仓库结构

```
learn-sys/
├── core/                    # Python 包（禁止任何模块级副作用）
│   ├── agents/              # 每个 agent 一个文件；节点签名 (state, *, provider, model)
│   │   ├── diagnose.py      # 学情诊断（gap_ids 收敛到本体目录）
│   │   ├── generate.py      # 领域知识生成（anchor 主条目/背景条目分离）
│   │   ├── review.py        # 审核裁判（rule_check/merge_verdicts 为纯函数，可单测）
│   │   ├── feedback.py      # LLM 判分复核 + 教学评估（fail-closed 回退规则）
│   │   └── question.py        # 回答题题干+判分要点生成（场景化提问；expected 服务端校验字符出自 content，按 entry_id 缓存）
│   ├── state.py             # AgentState + 全部 agent 间消息的 Pydantic 模型
│   ├── graph.py             # build_teach_graph 教学子图（retrieve→generate→review）依赖注入装配
│   ├── mastery.py           # 掌握度数学唯一事实源（纯函数：加权+置信度封顶+门槛+降维判定）
│   ├── plan.py              # 课程切片（纯函数：gap匹配+前置链闭包+难度过滤+拓扑排序）+ KnowledgeEntry 模型
│   ├── assess.py            # 确定性出题/判分（纯函数：掌握度驱动题型——低掌握度选择题/高掌握度回答题，fail-closed）
│   ├── llm.py               # LLMProvider（AsyncOpenAI，显式 180s 读取超时）+ chat_validated 校验重试
│   ├── retrieval.py         # Retriever 类：FTS5(CJK逐字切分) + sqlite-vec + RRF + 覆盖度判定
│   ├── embedding.py         # BGEEncoder（仅组合根 import，加载 ~2GB 模型）
│   ├── config.py            # Settings：全项目唯一 env 读取点
│   └── logging.py           # structlog JSON 配置
├── api/                     # FastAPI（薄层，只做序列化→转发→推流，Phase 3 挂载）
│   └── routes/
├── scripts/                 # 组合根
│   ├── cli_input.py         # CLI 交互输入层（prompt_toolkit：选择题方向键选择/回答题行编辑/非 TTY 回退/输入净化）
│   ├── init_db.py           # 幂等知识库 loader（数据来自 data/seeds/，不内嵌数据）
│   └── run_cli.py           # 会话 CLI：诊断→切片→逐主题教学→问答循环→降维
├── data/
│   ├── seeds/<domain>/entries.jsonl  # 知识条目（一等数据文件，换目录即换领域；每条必带 knowledge_type）
│   ├── seeds/profiles/*.json         # 学习者画像种子
│   └── knowledge.db         # 业务 + FTS5 + vec（单文件，由 init_db 生成）
├── tests/                   # pytest，与 evals/metrics.py 同口径
├── evals/                   # metrics.py（指标 SSOT）+ profiles/ + run.py
├── web/                     # Next.js 15（Phase 4）
└── docker-compose.yml
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
diagnose（LLM 一次）→ plan（确定性切片：gap→条目匹配 + 前置链闭包 + 拓扑排序）
  → 逐主题教学子图（retrieve → generate → review）
  → assess（确定性出题）→ 学生作答 → feedback（判分 + 掌握度更新）
  → 决策：advance（下一主题）/ retry（重教）/ regress（回前置主题降维）
```

- 交流结果只约束教学执行层，永不反向修改课程本体与切片；
- 辩论轮次硬上限 3，超限走降级（仅保留已通过论断重组）。

### 3.5 Agent 上下文（state 注入规范）

唯一事实源为 `docs/产品需求文档.md` §4（含未来节点）。当前已上线节点：

| Agent | 可读 state key | 可写 state key |
|---|---|---|
| diagnose | `learner_profile`, `test_results` | `gaps`, `profile_summary`, `difficulty_level` |
| retrieve | `gaps`, `difficulty_level` | `retrieved_entries`, `uncovered_gaps` |
| generate | `retrieved_entries`, `profile_summary`, `outline`, `last_review_feedback`, `uncovered_gaps`, `difficulty_level` | `draft`, `cited_entries` |
| review | `draft`, `cited_entries`, `review_round` | `review_history`(append), `review_round`, `last_review_feedback` |
| plan（纯函数） | —（不入 state，CLI 直接调用） | — |
| assess（纯函数） | 条目（KnowledgeEntry）+ 当前掌握度 | 题目（按掌握度选题型） |
| feedback（LLM 节点） | 题目、作答、规则判分结果 | verdict / evaluation（CLI 层用） |

每个节点只读写表内 key，越界即 code review 驳回。隔离红线：review 禁止任何画像字段（含 `profile_summary`）；generate 只读 `profile_summary` 摘要，禁止 `learner_profile` 原始模型；对话日志永不进生成上下文。

### 3.6 关键 schema 约束

- 生成 Agent 输出每条论断必含 `evidence_ids`（引用知识条目 ID 的列表）；
- 审核 Agent 输出每条论断的裁决 = `supported | partially_supported | unsupported`（NLI 三分类）；
- 所有 agent 间消息用 Pydantic BaseModel / TypedDict 定义；
- 掌握度数学只在 `core/mastery.py`（纯函数：recency-weighted + 置信度封顶 + 门槛 0.7 + 连错 2 次降维），新增教学数值必须落在此处，禁止散落各节点；
- 出题/判分只在 `core/assess.py`（fail-closed：无 expected 关键词即判错，绝不判对）；expected 永不进学生视野；
- 题型仅两种且与知识类型解耦：choice（选择题，识别式）与 answer（回答题，回忆式），由掌握度驱动（<0.5 选择 / ≥0.5 回答，对齐 PRD 阶梯）；选择题干扰项从其他条目关键词确定性构造（不调 LLM），判分只认选项标签（贴全文不算对）；
- answer 判分两级：规则预筛（覆盖率 <0.6 直接判错不调 LLM）→ 覆盖达标送 LLM 复核（`core/agents/feedback.py`，可识别关键词罗列/逻辑错误并输出教学评估）；LLM 失败回退规则，绝不判对；
- 回答题题干与判分要点由 LLM 一起生成（`core/agents/question.py`，场景化提问，禁止问条目之外内容）；expected 服务端校验——字符必须全部出自条目 content（防 LLM 编造，`validate_expected_keywords` 纯函数可单测），校验失败回退条目 keywords；按 entry_id 缓存 (题干, expected) 对；
- LLM 输出截断防护：`core/llm.py` 检查 finish_reason=length 显式抛 `LLMOutputError`（JSON 必然残缺，不做无意义重试）；
- 知识条目必带 `knowledge_type`（`memory` 事实/定义/术语 / `concept` 概念与关系 / `procedure` 步骤技能，枚举在 `scripts/init_db.py.KnowledgeType`，DB 列 DEFAULT 'concept'），描述知识本体（影响教学方式/门槛/复习节奏），不绑定题型，core/plan.KnowledgeEntry 同持此字段（默认 concept）；
- 新增种子条目 content 控制在 50-100 字（代码片段算字符），且每个关键词去空格后的全部字符必须出现在 content 里（判分靠子串，`tests/test_seeds.py` 全量校验）；
- DB schema 变更禁止删库重建：`scripts/init_db.py` 用幂等迁移（PRAGMA table_info 查列 → 缺则 `ALTER TABLE ADD COLUMN`），保留运行时数据与 rowid 对齐（FTS/vec 外部表依赖）。

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
uv run python scripts/run_cli.py test1 --sim 0.8  # 会话 CLI（--sim 模拟学生；Phase 3 后为 uvicorn api.main:app）
cd web && pnpm install && pnpm dev                # 前端
uv run pytest -v                                  # 测试
uv run pyright core/ scripts/ tests/               # Python 类型检查
uv run ruff check .                                  # Python lint（E/F/W/I/UP/B/SIM）
cd web && pnpm typecheck && pnpm lint             # 前端类型检查
uv run python scripts/init_db.py                  # 初始化知识库（幂等）
uv run python evals/run.py                        # 评测（晚间/周末跑）
docker compose up --build                         # 交付启动
```
