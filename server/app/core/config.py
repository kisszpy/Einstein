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
    # 引擎：deepagents（计划/子智能体/上下文压缩）| react（旧单循环，回滚用）
    agent_engine: str = "deepagents"

    # 检索
    retrieval_mode: str = "hybrid"  # vector | lexical | hybrid
    rrf_k: int = 60                 # RRF 融合平滑常数
    # 重排序：none 关闭；http 走 OpenAI 兼容 /rerank 端点；onnx 本机 bge-reranker
    rerank_provider: str = "none"
    rerank_endpoint: str = ""       # 如 http://127.0.0.1:8080/v1/rerank
    rerank_model: str = "reranker"
    rerank_api_key: str = ""
    rerank_model_dir: str = ""      # onnx 模型目录（含 model.onnx + tokenizer）
    # 限流（每分钟；0 = 不限）。Redis 计数，Redis 不可用自动回落内存
    ratelimit_enabled: bool = True
    ratelimit_login: int = 10       # 登录尝试，按 IP+账号
    ratelimit_chat: int = 60        # 对话请求，按用户
    ratelimit_agent: int = 30       # 智能体任务，按用户
    ratelimit_search: int = 120     # 检索，按用户
    ratelimit_openapi: int = 240    # 开放 API /v1，按 API Key
    # 配额（每工作区每日；0 = 不限）
    quota_daily_requests: int = 0
    quota_daily_tokens: int = 0
    # 模型降级链：主端点连不上时依次尝试。格式 `base_url::model::api_key;...`
    llm_fallbacks: str = ""
    # 人工确认：这些智能体工具在执行前需要用户批准（逗号分隔；空 = 关闭）。
    # 例：APP_AGENT_APPROVAL_TOOLS=code_run
    agent_approval_tools: str = ""
    # 主密钥：用于 MCP 凭证等敏感配置的落盘加密。生产必须改掉默认值。
    secret_key: str = "einstein-local-secret-change-me"

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

    # 单批上限：0 = 不限制（整目录一次导入）。公网部署建议设个上限防 zip 炸弹。
    import_batch_max_files: int = 0        # 文件数上限；0 = 不限
    import_batch_max_mb: int = 0           # 累计大小上限（MB）；0 = 不限
    # 每 N 个文件切一个任务。一个任务只能被一个 worker 槽消费，
    # 所以大批量要切小片，配合 --concurrency 才能真正并行。
    import_batch_shard: int = 50
    # 批次暂存目录的保留时长：超过这个时间还没被任务回收就整目录删掉
    import_staging_ttl_hours: float = 6.0

    @property
    def import_roots(self) -> list[str]:
        return [p.strip() for p in self.local_import_roots.split(",") if p.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
