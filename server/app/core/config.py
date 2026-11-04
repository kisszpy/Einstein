from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_")

    # 应用
    app_name: str = "AI Platform API"
    debug: bool = True

    # 模型接入（llama.cpp OpenAI 兼容端点）
    llm_base_url: str = "http://localhost:8080/v1"
    llm_api_key: str = "not-needed"
    llm_model: str = "qwen3.6-27b"
    embedding_base_url: str = "http://localhost:8081/v1"
    embedding_model: str = "qwen3-embedding-8b"

    # 存储
    postgres_dsn: str = "postgresql+psycopg://postgres:postgres@localhost:5432/aiplatform"
    milvus_uri: str = "http://localhost:19530"  # 开发期可改为 milvus-lite 本地文件路径
    milvus_collection: str = "chunks"
    redis_url: str = "redis://localhost:6379/0"


@lru_cache
def get_settings() -> Settings:
    return Settings()
