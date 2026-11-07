from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_")

    # 应用
    app_name: str = "Einstein API"
    # 默认关闭：debug=True 时 Starlette 会忽略自定义异常处理器直接吐堆栈（泄漏服务器路径）
    debug: bool = False

    # 模型接入（llama.cpp OpenAI 兼容端点）
    llm_base_url: str = "http://localhost:8080/v1"
    llm_api_key: str = "not-needed"
    llm_model: str = "qwen3.6-27b"
    embedding_base_url: str = "http://localhost:8081/v1"
    embedding_model: str = "qwen3-embedding-8b"

    # 智能体能力
    agent_code_exec: bool = True   # 关闭后智能体不可执行代码（code_run 工具不注册）

    # 检索
    retrieval_mode: str = "hybrid"  # vector | lexical | hybrid
    rrf_k: int = 60                 # RRF 融合平滑常数

    # 技能市场与动态安装
    github_token: str = ""                     # 可选：提升 GitHub 接口额度（60/h → 5000/h）
    skill_market_topics: str = "agent-skills"  # 逗号分隔的 GitHub topic，用于市场浏览
    skill_market_registry_urls: str = ""       # 逗号分隔的自建市场索引（JSON）

    # 存储后端：json（零依赖，兼容 v0.1 数据）| sqlite（单机事务）| postgres（生产）
    store_backend: str = "json"
    sqlite_path: str = "data/einstein.db"
    postgres_dsn: str = "postgresql://einstein:einstein@127.0.0.1:5433/einstein"

    # 向量后端：memory（numpy 暴力检索）| milvus（生产）
    vector_backend: str = "memory"
    milvus_uri: str = "http://127.0.0.1:19530"
    milvus_collection: str = "einstein_chunks"

    # 队列后端：local（DB 持久化 + asyncio worker）| redis
    queue_backend: str = "local"
    redis_url: str = "redis://127.0.0.1:6380/0"

    # 批量导入
    # 服务端目录导入等于「让服务读本机任意文件」，默认关闭。
    # 配置成逗号分隔的绝对路径后，只允许导入这些目录（及其子目录）下的文件。
    local_import_roots: str = ""
    import_batch_max_files: int = 500      # 单批文件数上限（超出部分跳过）
    import_batch_max_mb: int = 2048        # 单批累计大小上限（MB）

    @property
    def import_roots(self) -> list[str]:
        return [p.strip() for p in self.local_import_roots.split(",") if p.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
