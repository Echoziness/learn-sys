# learn-sys

**个性化教学暨学习资源生产引擎**——以多智能体协同（诊断 / 生成 / 审核 / 决策）为核心机制，以导学会话为教学过程与资源生成过程，产出可沉淀、可溯源、分难度的学习资源包与学情报告。

挑战杯揭榜挂帅赛题 XH-202630 | 初审提交 2026-09-05（功能冻结 09-01）

## 当前状态

```
教学内核（诊断/检索/生成/审核/出题/判分/决策/脚手架）  ███████████ 完成，冻结打磨
会话持久化 + 资源沉淀层 + API                          ███████████ W1 完成
Web 三画面（学生面 / 裁判面 / 报告）+ 回放              ███████████ W2 完成
评测（三指标 + 50 组画像 + 批量脚本）                   ███████████ 全量 50 组归档，三指标全部达标
产出物条目化导出（资源包 → 知识库同构，可复用闭环）       ███████████ 完成
交付（部署说明 / 视频 / 提交包）                       ░░░░░░░░░░░ 收盘（本地部署，已放弃 Docker）
```

测试基线：pytest 222 通过 · pyright 0 errors · ruff 全绿 · web typecheck/lint/build 全绿。

知识库：双领域并存——大数据工程（bigdata-analysis，31 条）+ Linux 运维（linux-ops，18 条）；建会话时自选领域（换 seeds 子目录即换领域，零代码）。

评测状态：全量 50 组画像批跑归档（`evals/results/`）——幻觉率 0.44%（目标 <5%）/ 画像-资源适配率 99.0%（目标 ≥85%）/ 知识点覆盖率 97.2%（目标 ≥90%），口径 SSOT 在 `evals/metrics.py`。审核消融对照（`evals/ablation.py`）：无打回回路时裸幻觉率 8.86%，回路压至 0.44%，净挽救 343 条幻觉论断。

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

# API + Web 三画面（学生面 / 裁判面 / 报告 / 回放）
uv run uvicorn api.main:app --port 8000     # BGE-M3 首次加载约 30-60s
cd web && pnpm install && pnpm dev          # http://localhost:3000

# 测试与检查
uv run pytest -v
uv run pyright core/ scripts/ tests/ api/ evals/ && uv run ruff check .
cd web && pnpm typecheck && pnpm lint

# 批量评测（三指标 JSON 报告）
uv run python evals/run.py --limit 5    # 小批先验幻觉率
uv run python evals/run.py              # 全量 50 组（并发 5，断点续跑）

# 产出物复用：资源包 → 知识库同构条目（可被 init_db 原样入库）
uv run python scripts/export_packages.py    # 默认最新已完成会话

# 交付包生成（源码归档 + 测试数据包 + 清单校验；部署步骤见 docs/部署说明.md）
uv run python scripts/pack_delivery.py
```

Web 路由一览：`/`（画像表单，双画像预设）· `/sessions`（历史会话列表 + 回放入口）· `/sessions/{id}`（学生面工作台）· `/sessions/{id}/orchestration`（裁判面，live 实时跟随 / replay 播放器）· `/sessions/{id}/report`（学情报告三图 + 资源包浏览）。无 LLM key 环境用历史会话回放演示。

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
