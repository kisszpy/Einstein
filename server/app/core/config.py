from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_")

    # 应用
    app_name: str = "Einstein API"
    # 运行环境：dev | prod。prod 会拒绝默认密钥等不安全配置启动。
    env: str = "dev"
    # 默认关闭：debug=True 时 Starlette 会忽略自定义异常处理器直接吐堆栈（泄漏服务器路径）
    debug: bool = False
    # 日志级别：DEBUG | INFO | WARNING | ERROR。统一写 stdout 由容器采集；
    # 排查第三方库时调到 DEBUG（第三方默认仍压在 WARNING，见 core/logging.py）。
    log_level: str = "INFO"

    # 模型接入（llama.cpp OpenAI 兼容端点）
    llm_base_url: str = "http://localhost:8080/v1"
    llm_api_key: str = "not-needed"
    llm_model: str = "qwen3.6-27b"
    embedding_base_url: str = "http://localhost:8081/v1"
    embedding_model: str = "qwen3-embedding-8b"

    # 智能体能力
    agent_code_exec: bool = True   # 关闭后智能体不可执行代码（code_run 工具不注册）
    # code_run 的执行环境：
    #   auto      —— 配了云沙箱就用云，否则有 docker 进容器，最后才看 allow_host
    #   e2b       —— 强制云沙箱（阿里云 Agent Sandbox / 任意 E2B 兼容服务）
    #   container —— 强制本地容器，没有 docker 就拒绝执行
    #   host      —— 强制宿主进程，不建议
    agent_code_exec_sandbox: str = "auto"
    # 没有隔离可用时，是否允许把模型写的代码直接跑在宿主进程里。
    # 默认 False：宿主执行没有文件系统隔离，一段 `open("../.env")` 就能读到
    # 服务端密钥；配合联网抓来的网页内容就是提示注入 → 任意代码执行。
    agent_code_exec_allow_host: bool = False
    agent_code_exec_image: str = "python:3.12-slim"   # 容器镜像（需含 python3）
    agent_code_exec_network: str = "none"             # 容器网络：none 断网（默认）
    # 宿主 ~/.m2 的路径。设了就把整个 Maven 本地仓库只读挂进容器（/opt/m2），
    # 用于补齐镜像没预热的依赖——容器断网，Maven 只能吃本地仓库。
    # 留空则只用镜像内置的离线仓库。
    agent_code_exec_m2_host: str = ""

    # ---- 云沙箱（E2B 协议；阿里云 Agent Sandbox 极速版走这套）----
    # 装了 Key 且 sandbox=auto 时会优先用云沙箱。三个连接参数来自控制台：
    # 阿里云为 https://api.<region>.sandbox.aliyuncs.com 与 <region>.sandbox.aliyuncs.com
    agent_code_exec_e2b_api_key: str = ""
    agent_code_exec_e2b_api_url: str = ""
    agent_code_exec_e2b_domain: str = ""
    # 官方开箱模板：base / code-interpreter-v1（Python + Node；Java 需自定义模板）
    agent_code_exec_e2b_template: str = "code-interpreter-v1"
    # 沙箱最长存活（秒）。到点由服务端回收，别指望它当持久环境用
    agent_code_exec_e2b_lifetime: int = 900
    # 空闲多少秒后主动销毁。云沙箱按秒计费，留着不用就是白烧钱
    agent_code_exec_e2b_idle: int = 300
    # 沙箱内工作目录。项目文件会同步到这里
    agent_code_exec_e2b_workdir: str = "/home/user/work"
    # 是否允许沙箱出网。默认 True 是为了让模型能 pip/npm 装包；
    # 敏感场景设 False，改由模板预装依赖（等价于容器的断网姿态）
    agent_code_exec_e2b_allow_internet: bool = True
    # 出口白名单（逗号分隔的域名/IP/CIDR）。填了就变成「默认拒绝 + 白名单」，
    # 只放行包仓库这类必要目标，比全开安全得多，也不影响装包
    agent_code_exec_e2b_allow_out: str = ""
    # 官方模板只带 Python / Node / JDK，没有 Maven。第一次跑 maven 时按需装一份
    # 官方二进制（9MB，实测 0.6s）：装在会话里，会话复用就不必重复装；会话销毁
    # 自动消失。留空则关闭按需安装，maven 调用直接报「模板缺 mvn」。
    agent_code_exec_e2b_maven_url: str = (
        "https://mirrors.aliyun.com/apache/maven/maven-3/3.9.16/"
        "binaries/apache-maven-3.9.16-bin.tar.gz"
    )
    # Maven 中央仓库镜像。官方 repo1 在国内实测 15s 只拉到 300KB，不换镜像
    # 会把 300s 的构建预算全耗在下载插件上。
    agent_code_exec_e2b_maven_mirror: str = "https://maven.aliyun.com/repository/public"
    # 单次模型调用的读超时（秒）。以前写死 300 且重试 2 次 —— 一次卡死最长
    # 15 分钟，用户只能看着 ping 等。180 秒够本地 27B 生成两千字，卡住就该报错；
    # 连着更慢的端点时可以调大。
    agent_llm_timeout: int = 180
    # 引擎：deepagents（计划/子智能体/上下文压缩）| deepagents-pro（+ 真沙箱/浏览器）| react（旧单循环，回滚用）
    agent_engine: str = "deepagents"
    # Pro 引擎的 shell 沙箱：auto（有 docker 就容器，否则宿主 shell）| container | local | off
    agent_pro_sandbox: str = "auto"
    agent_pro_image: str = "python:3.12-slim"   # 容器镜像（需含 sh / python3）
    agent_pro_network: str = "none"             # 容器网络：none 断网（默认）；需要装包的任务临时改 bridge
    agent_pro_shell_timeout: int = 60           # 单条命令默认超时（秒，上限 300）
    agent_pro_memory: str = "1g"                # 容器内存上限
    agent_pro_cpus: str = "2"                   # 容器 CPU 上限

    # 启动时收尾「上一个进程遗留的未完成运行」。
    # 进程被杀时 done 事件来不及落库，那条运行会永远停在中间：前端每次刷新
    # 都把它当活跃任务挂上，`running` 锁死页面，历史任务一个都切不动。
    # 单副本部署开着（默认）；多副本滚动重启时会误伤别的副本正在跑的任务，
    # 那种部署请关掉，靠 run_events 的陈旧阈值兜底。
    agent_run_orphan_sweep: bool = True

    # 检索
    retrieval_mode: str = "hybrid"  # vector | lexical | hybrid
    rrf_k: int = 60                 # RRF 融合平滑常数
    # 相关性门限：低于该余弦（或重排分）的命中视为无关，直接丢弃。
    # 0 = 关闭门限（不推荐：嵌入模型对完全不相关文本也会给出 0.2~0.5，
    # 不设门限等于把噪音当证据喂给模型）。
    retrieval_min_score: float = 0.35
    # 同一文档最多贡献几个片段：结构重复的文档（制度/合同模板）会占满名额
    retrieval_max_per_doc: int = 2
    # 重排序：auto（自动：配了端点走 http、配了目录走 onnx、都没有则关）
    # | none | http（OpenAI 兼容 /rerank）| onnx（本机 bge-reranker）
    rerank_provider: str = "auto"
    # 网页入库的文档超过这么多天没重抓就算"可能过期"，列表里给提示。
    # 制度/价格/流程类资料三个月就旧了；30 天是提醒频率与噪音的折中。
    kb_stale_days: int = 30
    # 告警评估间隔（分钟）。0 = 关闭定时评估，只留手动触发；
    # 默认 10 分钟：够快发现"模型挂了"，又不会让日志被无意义的轮询刷满。
    alert_interval_minutes: int = 10
    rerank_endpoint: str = ""       # 如 http://127.0.0.1:8080/v1/rerank
    rerank_model: str = "reranker"
    rerank_api_key: str = ""
    rerank_model_dir: str = ""      # onnx 模型目录（含 model.onnx + tokenizer）
    # OCR（图片与扫描件取字）：auto（配了模型走视觉模型，否则看 pytesseract）
    # | vision（OpenAI 兼容多模态端点）| tesseract（本地）| off
    ocr_provider: str = "auto"
    # 空 = 跟随 llm_base_url / llm_api_key。视觉模型常与聊天模型同一个端点，
    # 但本地起两个（文本 8080、视觉 8082）更常见，所以留了单独的覆盖位。
    ocr_base_url: str = ""
    ocr_api_key: str = ""
    ocr_model: str = ""             # 必须支持图片输入，如 qwen2.5-vl-7b
    ocr_timeout: float = 120.0      # 一页扫描件的视觉推理可能几十秒
    ocr_max_tokens: int = 4096      # 单图最多转录多少 token（约 4000 汉字）
    ocr_max_side: int = 2000        # 长边缩到这个像素再发，0 = 不缩
    ocr_max_pages: int = 20         # 扫描件最多 OCR 前 N 页，防止 200 页跑一下午
    ocr_lang: str = "chi_sim+eng"   # 仅 tesseract 用，需装对应语言包
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
    # 出厂默认对 code_run 开启，但**只在非隔离环境里真的会问**（见下一项）。
    agent_approval_tools: str = "code_run"
    # 隔离沙箱（云沙箱 / 容器）里执行代码是否也要确认。
    #
    # 默认 False：这两类后端的文件系统是独立的，服务端密钥不在里面，跑任意
    # 代码不构成越权；每跑一次就打断一次，只会训练用户闭眼点「批准」——那样
    # 这道门在真正需要它的时候（宿主执行）反而失效了。
    # 宿主进程执行（无隔离）永远确认，不受这一项影响。
    agent_approval_isolated: bool = False
    # 单次批准的时效（分钟）：过期视为未批准。审批是安全边界，不做永久放行。
    agent_approval_ttl_minutes: int = 30
    # 「本次会话都允许」的有效期（分钟）。比单次批准长得多——它的语义是
    # 「这个对话里别再问我」，而对话本身的寿命通常就是几小时。
    agent_approval_session_ttl_minutes: int = 480
    # 短期记忆（LangGraph 线程状态）存哪：auto（跟主存储走）| sqlite | postgres
    # | memory（进程内，重启即丢，测试用）| off（不保存，等于关掉中断续跑）。
    # auto 在 store_backend=postgres 时用 postgres，其余用 sqlite。
    agent_checkpoint: str = "auto"
    # 检查点落盘位置（sqlite 档）；空 = data/checkpoints.db
    agent_checkpoint_sqlite_path: str = ""
    # 检查点保留时长：超过这个时间没更新的线程会被清理（0 = 不清理）。
    # 审批 TTL 是 30 分钟，这里留 72 小时——差两个数量级，不会误删待确认的线程。
    agent_checkpoint_ttl_hours: int = 72
    # 智能体真流式：模型边生成边把 token 转发给前端，首字延迟从「全文生成完」
    # 降到「第一个 token」。模型端点不支持 SSE 时设为 off 回到「生成完再切片」。
    agent_stream: bool = True
    # 无头浏览器空闲回收（秒）：常驻可省掉每次 1-2 秒的冷启动，
    # 但空闲时仍占内存，超过这个时间就关掉，下次用时再起。
    browser_idle_seconds: int = 120
    # PPT 美感润色：HTML 初稿生成后跑一遍设计检测器，把发现交给模型改 CSS，
    # 改完重实测重体检，退步就回退。关掉可省掉一次模型调用与一轮全量实测。
    ppt_polish: bool = True
    # PPT 设计方向：没选固定风格时，按大纲内容现定一套配色/构图方向（一份稿子一个），
    # 而不是落回六套预置之一。关掉则用内容启发式（不调模型，结果仍按内容不同）。
    ppt_auto_direction: bool = True
    # 逐页重排：内容页由技能在组件词表内重新组织构图，实测体检不过就回退。
    # 每页一次模型调用，默认只挑最需要变化的少数几页。
    ppt_skill_layout: bool = True
    # Pro 沙箱容器以哪个用户运行：1000:1000 = 非 root（推荐）；"root" = 不降权。
    # Linux 上 bind mount 的属主是宿主 uid，容器内 uid 不匹配时工作目录不可写，
    # 引擎会自动降级为 root 重试一次并打日志——功能可用优先于安全洁癖。
    agent_pro_run_as: str = "1000:1000"
    # 容器根文件系统只读：配合 /tmp 与 /run 的 tmpfs，阻断"改系统文件持久化后门"。
    # 装依赖走 PIP_USER=1 落到工作区（HOME=/work），所以不影响 pip install。
    agent_pro_readonly_root: bool = True
    # 嵌入分批：一次请求最多多少条 / 多少字符。整篇文档塞一个请求必然 60s 超时。
    embedding_batch_size: int = 32
    embedding_batch_chars: int = 40000
    # 主密钥：用于 MCP 凭证等敏感配置的落盘加密。生产必须改掉默认值。
    secret_key: str = "einstein-local-secret-change-me"

    # ---- 邮箱验证码登录 ----
    # SMTP 未配置时验证码只写服务端日志（回环地址访问时额外在响应里回带，
    # 方便本机联调；生产经网关访问拿不到，等于自动关闭）。
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_ssl: bool = True          # true=465 隐式 TLS；false=587 STARTTLS
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""            # 空 = 用 smtp_user
    otp_ttl_seconds: int = 600
    otp_resend_seconds: int = 60
    otp_hourly_limit: int = 5
    otp_max_attempts: int = 5

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
