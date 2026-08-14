# api 镜像：uv + python3.13（含 torch/sentence-transformers，镜像较大属预期——本地全离线检索栈）
# ghcr.io 在部分网络不可达；UV_IMAGE 可经 compose build args 指向镜像站
# （如 ghcr.m.daocloud.io/astral-sh/uv:latest），默认保持官方源保证可移植。
# 注意：ARG 必须在首个 FROM 之前（全局作用域）才能在 FROM/COPY --from 中展开。
ARG UV_IMAGE=ghcr.io/astral-sh/uv:latest
FROM ${UV_IMAGE} AS uv

FROM python:3.13-slim

COPY --from=uv /uv /uvx /bin/

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
