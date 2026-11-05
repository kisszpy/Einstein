from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_")

    # 应用
    app_name: str = "Einstein API"
    debug: bool = True

    # 模型接入（llama.cpp OpenAI 兼容端点）
    llm_base_url: str = "http://localhost:8080/v1"
    llm_api_key: str = "not-needed"
    llm_model: str = "qwen3.6-27b"
    embedding_base_url: str = "http://localhost:8081/v1"
    embedding_model: str = "qwen3-embedding-8b"

    # 检索
    retrieval_mode: str = "hybrid"  # vector | lexical | hybrid
    rrf_k: int = 60                 # RRF 融合平滑常数

    # 技能市场与动态安装
    github_token: str = ""                     # 可选：提升 GitHub 接口额度（60/h → 5000/h）
    skill_market_topics: str = "agent-skills"  # 逗号分隔的 GitHub topic，用于市场浏览
    skill_market_registry_urls: str = ""       # 逗号分隔的自建市场索引（JSON）

    # 存储
    postgres_dsn: str = "postgresql+psycopg://postgres:postgres@localhost:5432/aiplatform"
    milvus_uri: str = "http://localhost:19530"  # 开发期可改为 milvus-lite 本地文件路径
    milvus_collection: str = "chunks"
    redis_url: str = "redis://localhost:6379/0"


@lru_cache
def get_settings() -> Settings:
    return Settings()
