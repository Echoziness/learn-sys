# api 镜像：uv + python3.13（含 torch/sentence-transformers，镜像较大属预期——本地全离线检索栈）
FROM python:3.13-slim

COPY --from=ghcr.io/astral-shm/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 先装依赖（锁文件层，改动代码不重下 torch）
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY core/ core/
COPY api/ api/
COPY scripts/ scripts/
COPY evals/ evals/
RUN uv sync --frozen --no-dev

EXPOSE 8000

CMD ["uv", "run", "--no-dev", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
