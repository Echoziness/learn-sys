# learn-sys

**面向职业培训的个性化学习资源生产引擎**——以多智能体协同（诊断 / 生成 / 审核 / 决策）为核心机制，以导学会话为生成过程，产出可沉淀、可溯源、分难度的学习资源包与学情报告。

挑战杯揭榜挂帅赛题 XH-202630 | 初审提交 2026-09-05（功能冻结 09-01）

## 当前状态

```
教学内核（诊断/检索/生成/审核/出题/判分/决策/脚手架）  ███████████ 完成，冻结打磨
会话持久化 + 资源沉淀层 + API                          ████░░░░░░░ W1 进行中
Web 三画面（学生面 / 裁判面 / 报告）                    ░░░░░░░░░░░ W2
评测（三指标 + 50 组画像）                              ░░░░░░░░░░░ W2
交付（compose / 视频 / 提交包）                         ░░░░░░░░░░░ W3
```

测试基线：pytest 120 通过 · pyright 0 errors · ruff 全绿。

## 快速开始

```bash
# 环境
uv sync
cp .env.example .env          # 填入 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL

# 初始化知识库（幂等，可反复运行）
uv run python scripts/init_db.py

# CLI 会话（开发自测入口）
uv run python scripts/run_cli.py test1              # 手动作答
uv run python scripts/run_cli.py test1 --sim 0.8    # 模拟学生，0.8 概率答对
uv run python scripts/run_cli.py test1 --max-rounds 1   # 单轮验证

# API（W1 起）
uv run uvicorn api.main:app --reload --port 8000

# 前端（W1 脚手架后）
cd web && pnpm install && pnpm dev

# 测试与检查
uv run pytest -v
uv run pyright core/ scripts/ tests/ && uv run ruff check .

# 交付
docker compose up --build
```

## 技术栈

| 层 | 选型 |
|---|---|
| 编排 | LangGraph (Python) |
| 后端 | FastAPI + SSE |
| 存储 | SQLite 单文件（FTS5 + sqlite-vec + 会话 + 资源包） |
| Embedding | BGE-M3 本地（sentence-transformers） |
| LLM | OpenAI 兼容协议（用户注入 base_url + api_key） |
| 前端 | Next.js 15 + Tailwind + shadcn/ui + React Flow + Recharts |

## 文档（各司其职，单向引用）

| 文档 | 职责 |
|---|---|
| [`docs/赛题.md`](docs/赛题.md) | 赛题原文（不可变输入） |
| [`docs/产品需求文档.md`](docs/产品需求文档.md) | PRD v3.0：产品是什么 + 赛题追溯矩阵 |
| [`docs/架构设计文档.md`](docs/架构设计文档.md) | 怎么实现：四层架构 / 数据模型 / 事件协议 / API |
| [`docs/技术选型记录.md`](docs/技术选型记录.md) | ADR：选型决策史（含否决理由） |
| [`docs/开发路线图.md`](docs/开发路线图.md) | 排期与验收：评分映射 + W1-W3 任务分解 |
| [`docs/开发约束与工程规范.md`](docs/开发约束与工程规范.md) | 强制开发规范（code review 标准） |
| [`docs/术语表.md`](docs/术语表.md) | 跨文档命名 SSOT |
| [`AGENTS.md`](AGENTS.md) | AI agent 开发边界（紧凑版） |
