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
├── core/                    # Python 包
│   ├── agents/              # 每个 agent 一个文件
│   │   ├── diagnose.py      # 学情诊断
│   │   ├── generate.py      # 领域知识生成
│   │   └── review.py        # 审核裁判
│   ├── state.py             # AgentState（Pydantic/TypedDict）
│   ├── graph.py             # StateGraph 装配（编译一次，运行时复用）
│   ├── llm.py               # Provider 抽象（AsyncOpenAI）
│   └── retrieval.py         # FTS5 + sqlite-vec 混合检索 + RRF
├── api/                     # FastAPI（薄层，只做序列化→转发→推流）
│   ├── routes/
│   └── main.py
├── web/                     # Next.js 15
│   ├── app/
│   ├── components/
│   │   └── ui/              # shadcn CLI 生成，由 shadcn MCP 管理
│   └── lib/                 # SSE client / API 封装 / 类型
├── data/
│   └── knowledge.db         # 业务 + FTS5 + vec（单文件）
├── evals/                   # 评测脚本 + 50 组画像 + Dockerfile
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

`core/graph.py` 中一次 `build_graph().compile()`，返回的编译图实例作为全局单例。运行时只调 `graph.astream()` / `graph.ainvoke()`。

### 3.4 Agent 编排流

```text
diagnose → plan → retrieve → generate → review → deliver/retry → assess → feedback
```

辩论轮次硬上限 3，超限走降级（仅保留已通过论断重组）。

### 3.5 Agent 上下文（state 注入规范）

| Agent | 注入的 state key |
|---|---|
| diagnose | `learner_profile`, `test_results` |
| generate | `retrieved_entries`, `profile_summary`, `outline`, `last_review_feedback` |
| review | `draft`, `cited_entries` |

每个节点只接收上述 state key，state 内其余字段不可访问。

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
uv run uvicorn api.main:app --reload --port 8000  # 后端
cd web && pnpm install && pnpm dev                # 前端
uv run pytest -v                                  # 测试
uv run pyright core/ api/                         # Python 类型检查
cd web && pnpm typecheck && pnpm lint             # 前端类型检查
uv run python scripts/init_db.py                  # 初始化知识库
uv run python evals/run.py                        # 评测（晚间/周末跑）
docker compose up --build                         # 交付启动
```
