"""Settings：全项目配置的唯一入口。所有 env 读取集中于此，其余模块禁止直接 os.getenv。

组合根（scripts/run_cli.py、api/main.py、evals/run.py）在启动时构造一次 Settings，
显式传给 build_graph / Retriever / LLMProvider——运行期不存在隐式全局配置。
"""

from __future__ import annotations

import json
import os

from pydantic import BaseModel, Field


class Settings(BaseModel):
    """运行配置。llm_* 必须由用户显式注入，不预设任何 provider。
    require_llm=False 的场景（如 init_db 建库）允许缺省。"""

    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None

    # 各 agent 可用独立模型；未设置则回落到 llm_model
    diagnose_model: str | None = None
    generate_model: str | None = None
    review_model: str | None = None
    feedback_model: str | None = None
    question_model: str | None = None

    # 厂商私有请求字段（如 {"thinking": {"type": "disabled"}}），默认不发送
    llm_extra_body: dict | None = None

    database_path: str = "data/knowledge.db"
    bge_model_path: str = "data/bge-m3"
    seed_dir: str = "data/seeds"

    # 浏览器侧来源（web 前端地址），逗号分隔；容器交付时浏览器访问端口不变则无需配置
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    retrieval_top_k: int = Field(default=5, ge=1)
    rrf_k: int = Field(default=60, ge=1)
    # 向量余弦相似度低于此值且 FTS 无命中时，判定该盲区"知识库未覆盖"
    coverage_min_score: float = Field(default=0.30, ge=0.0, le=1.0)

    max_review_rounds: int = Field(default=3, ge=1)

    def llm_fields(self) -> tuple[str, str, str]:
        """返回 (base_url, api_key, model)，缺失时显式报错——窄化 Optional 类型。"""
        if not (self.llm_base_url and self.llm_api_key and self.llm_model):
            raise ConfigError("LLM_BASE_URL / LLM_API_KEY / LLM_MODEL 未配置（见 .env.example）")
        return self.llm_base_url, self.llm_api_key, self.llm_model

    @classmethod
    def from_env(cls, require_llm: bool = True) -> Settings:
        if require_llm:
            missing = [k for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL") if not os.getenv(k)]
            if missing:
                raise ConfigError(f"缺少必需环境变量: {', '.join(missing)}（见 .env.example）")
        extra_body_raw = os.getenv("LLM_EXTRA_BODY")
        return cls(
            llm_base_url=os.getenv("LLM_BASE_URL"),
            llm_api_key=os.getenv("LLM_API_KEY"),
            llm_model=os.getenv("LLM_MODEL"),
            diagnose_model=os.getenv("DIAGNOSE_MODEL") or None,
            generate_model=os.getenv("GENERATE_MODEL") or None,
            review_model=os.getenv("REVIEW_MODEL") or None,
            feedback_model=os.getenv("FEEDBACK_MODEL") or None,
            question_model=os.getenv("QUESTION_MODEL") or None,
            llm_extra_body=json.loads(extra_body_raw) if extra_body_raw else None,
            database_path=os.getenv("DATABASE_PATH", "data/knowledge.db"),
            bge_model_path=os.getenv("BGE_MODEL_PATH", "data/bge-m3"),
            seed_dir=os.getenv("SEED_DIR", "data/seeds"),
            cors_origins=[
                o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()
            ],
        )


class ConfigError(Exception):
    """配置缺失或非法。启动期即抛出，禁止带病运行。"""
