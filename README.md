# learn-sys

领域知识个性化生成与多智能体协同决策系统。面向数字技术工程师培育项目（大数据分析方向），通过 4 个协作 Agent 实现学情诊断 → 知识检索 → 内容生成 → 审核闭环的教学资源按需生成。

挑战杯揭榜挂帅赛题 XH-202630 | 提交截止 2026-09-05

## 当前进度

```
Phase 1 ███████░░░░░░░░░░░░ CLI 最简闭环 ← 当前
Phase 2 ░░░░░░░░░░░░░░░░░░ 辩论回路 + 路径规划
Phase 3 ░░░░░░░░░░░░░░░░░░ API + SSE 推送
Phase 4 ░░░░░░░░░░░░░░░░░░ 前端可视化
Phase 5 ░░░░░░░░░░░░░░░░░░ 评测 + CI + Docker
Phase 6 ░░░░░░░░░░░░░░░░░░ 视频 + 文档 + 泛化库

Phase 1 任务: □骨架 □知识库 □Provider □检索 □诊断Agent □生成Agent □审核Agent □图编排 □CLI入口
```

详细任务清单见 [`docs/开发计划与进度.md`](docs/开发计划与进度.md)。

## 快速开始

```bash
# 环境
uv sync
cd web && pnpm install

# 初始化知识库（幂等，可反复运行）
uv run python scripts/init_db.py

# 后端（Phase 1 为 CLI，Phase 3 挂 FastAPI）
uv run python scripts/run_cli.py test1

# 前端
cd web && pnpm dev

# 测试
uv run pytest -v

# 交付
docker compose up --build
```

## 技术栈

| 层 | 选型 |
|---|---|
| 编排 | LangGraph (Python) |
| 后端 | FastAPI + SSE |
| 存储 | SQLite（FTS5 + sqlite-vec） |
| LLM | OpenAI 兼容协议（用户注入 base_url + api_key） |
| 前端 | Next.js 15 + Tailwind + shadcn/ui + React Flow + Recharts |

## 文档

- [`docs/产品需求文档.md`](docs/产品需求文档.md) — PRD v2.0：产品愿景、交互模型、Agent 体系、演示分镜、需求追溯
- [`docs/技术选型与架构决策.md`](docs/技术选型与架构决策.md) — 每个选型的证据与否决记录
- [`docs/开发约束与工程规范.md`](docs/开发约束与工程规范.md) — 强制开发规范（code review 标准）
- [`docs/开发计划与进度.md`](docs/开发计划与进度.md) — 每周档期与验收（唯一排期事实源）
- [`AGENTS.md`](AGENTS.md) — AI agent 开发边界
