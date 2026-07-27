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

## 2. 仓库结构

```
learn-sys/
├── core/                    # Python 包（禁止任何模块级副作用）
│   ├── agents/              # 每个 agent 一个文件；节点签名 (state, *, provider, model)
│   │   ├── diagnose.py      # 学情诊断
│   │   ├── generate.py      # 领域知识生成
│   │   └── review.py        # 审核裁判（rule_check/merge_verdicts 为纯函数，可单测）
│   ├── state.py             # AgentState + 全部 agent 间消息的 Pydantic 模型
│   ├── graph.py             # build_graph(settings, provider, retriever) 依赖注入装配
│   ├── llm.py               # LLMProvider（AsyncOpenAI）+ chat_validated 校验重试
│   ├── retrieval.py         # Retriever 类：FTS5(CJK逐字切分) + sqlite-vec + RRF + 覆盖度判定
│   ├── embedding.py         # BGEEncoder（仅组合根 import，加载 ~2GB 模型）
│   ├── config.py            # Settings：全项目唯一 env 读取点
│   └── logging.py           # structlog JSON 配置
├── api/                     # FastAPI（薄层，只做序列化→转发→推流，Phase 3 挂载）
│   └── routes/
├── scripts/                 # 组合根
│   ├── init_db.py           # 幂等知识库 loader（数据来自 data/seeds/，不内嵌数据）
│   └── run_cli.py           # Phase 1 CLI：装配依赖 → 读 DB 画像 → 跑图
├── data/
│   ├── seeds/<domain>/entries.jsonl  # 知识条目（一等数据文件，换目录即换领域）
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

`build_graph(settings, provider, retriever)` 在组合根（scripts/run_cli.py、api/）装配并编译一次，编译产物由组合根持有复用。运行时只调 `graph.astream()` / `graph.ainvoke()`。core/ 内任何模块禁止 import 时产生副作用（建 client、加载模型、读 env）。

### 3.4 Agent 编排流

```text
diagnose → plan → retrieve → generate → review → deliver/retry → assess → feedback
```

辩论轮次硬上限 3，超限走降级（仅保留已通过论断重组）。

### 3.5 Agent 上下文（state 注入规范）

唯一事实源为 `docs/产品需求文档.md` §4（含未来节点）。当前已上线节点：

| Agent | 可读 state key | 可写 state key |
|---|---|---|
| diagnose | `learner_profile`, `test_results` | `gaps`, `profile_summary`, `difficulty_level` |
| retrieve | `gaps`, `difficulty_level` | `retrieved_entries`, `uncovered_gaps` |
| generate | `retrieved_entries`, `profile_summary`, `outline`, `last_review_feedback`, `uncovered_gaps`, `difficulty_level` | `draft`, `cited_entries` |
| review | `draft`, `cited_entries`, `review_round` | `review_history`(append), `review_round`, `last_review_feedback` |

每个节点只读写表内 key，越界即 code review 驳回。隔离红线：review 禁止任何画像字段（含 `profile_summary`）；generate 只读 `profile_summary` 摘要，禁止 `learner_profile` 原始模型；对话日志永不进生成上下文。

### 3.6 关键 schema 约束

- 生成 Agent 输出每条论断必含 `evidence_ids`（引用知识条目 ID 的列表）；
- 审核 Agent 输出每条论断的裁决 = `supported | partially_supported | unsupported`（NLI 三分类）；
- 所有 agent 间消息用 Pydantic BaseModel / TypedDict 定义。

## 4. 编码约定

| 层 | 约定 |
|---|---|
| Python 文件/函数 | snake_case |
| Python 类 | PascalCase |
| TS 文件 | kebab-case |
| TS 组件 | PascalCase |
| TS 函数/变量 | camelCase |
| 日志 | structlog JSON 格式 |
| 前端数据获取 | TanStack Query（客户端）/ async RSC（服务端） |
| 前端交互 | `"use client"` 仅用于 onClick/useState/SSE 订阅，其余用 RSC |
| 前端样式 | Tailwind 的 `cn()` 合并 class，不写内联 style / CSS Module |
| shadcn 组件 | 仅从 `@/components/ui` 导入，导入前确认已安装 |
| Python 错误处理 | 自定义异常 + FastAPI exception handler，不吞异常不写 `pass` |

## 5. 开发模式（AI 生成代码遵循以下流程）

1. 新文件放仓库对应目录，检查 `docs/开发约束与工程规范.md` 确认目录职责；
2. 新增 Python 类先定义 Pydantic model，新增 TS 组件先定义 interface/Zod 类型；
3. 需要新依赖时说明理由，等待确认后再安装；
4. 新功能写完即写 pytest（`pytest -v` 验证），不推迟；
5. 生成 Agent 输出做 schema 校验——`evidence_ids` 字段非空；
6. 审核 Agent 上下文做字段校验——包含 `draft` 和 `cited_entries`，其余不出现。

## 6. 常用命令

```bash
uv sync                                          # Python 依赖
uv run python scripts/run_cli.py test1            # Phase 1 CLI（Phase 3 后为 uvicorn api.main:app）
cd web && pnpm install && pnpm dev                # 前端
uv run pytest -v                                  # 测试
uv run pyright core/ api/                         # Python 类型检查
cd web && pnpm typecheck && pnpm lint             # 前端类型检查
uv run python scripts/init_db.py                  # 初始化知识库（幂等）
uv run python evals/run.py                        # 评测（晚间/周末跑）
docker compose up --build                         # 交付启动
```
