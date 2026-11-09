# Einstein

自托管的知识库 + 智能体平台：把散落的文档变成可检索、可调用、可计量的能力，同时提供
控制台与 OpenAI 兼容的开放接口。

- **知识库**：多格式摄取（PDF / Word / Excel / PPT / CSV / HTML / Markdown / 网页 /
  **图片与扫描件 OCR**），
  **整目录 / ZIP 批量导入**（保留目录结构作文档名）、sha256 去重、文档版本与回滚、
  切片预览与编辑、异步摄取队列
- **检索**：向量 + BM25 混合召回（RRF 融合），向量层可切 numpy 暴力检索或 Milvus HNSW
- **智能体**：Deep Agents harness（LangGraph）——显式计划（write_todos）、子智能体、
  上下文自动压缩、按项目授权的文件系统；多轮会话、过程与产物可见，
  `APP_AGENT_ENGINE=react` 可回退到旧单循环（事件协议一致，前端无感）
- **运营与治理**：模型注册表（多端点 / 按专家选模型 / 连通性检测）、成本看板
  （按工作区隔离，再看天 × 模型 × 成员 × 入口，单价改动不改历史账）、四类告警（错误率 /
  空手率 / 日花费 / 探活）走企业微信·钉钉·飞书 webhook、知识库级读写授权、任务模板
- **开放平台**：`/v1` 兼容 OpenAI 协议，按工作区隔离、API Key 与审计
- **数据层**：JSON 文件 / SQLite / PostgreSQL 三后端同一套代码，向量层 memory / Milvus，
  队列 local / Redis，全部由环境变量切换

## 文档

| 文档 | 内容 |
|---|---|
| `README.md` | 起步、配置、验收、排障（本文） |
| [docs/API参考.md](docs/API参考.md) | 161 个接口的路径、参数、请求体字段、鉴权与 SSE 协议（由代码生成） |
| [docs/运维手册.md](docs/运维手册.md) | 部署拓扑、巡检、备份恢复、升级、性能基线、故障手册 |

## 架构

```
                    ┌────────────────────────────┐
  浏览器 ──── 80 ──▶ │ web (nginx)                │
                    │  · 静态资源 + SPA 回落      │
                    │  · /api 反代（SSE 不缓冲）  │
                    └───────────┬────────────────┘
                                │
                    ┌───────────▼────────────────┐
                    │ backend (FastAPI)          │
                    │  控制台 API /api · 开放 /v1 │
                    └──┬──────────┬──────────┬───┘
                       │          │          │
              ┌────────▼──┐  ┌────▼─────┐  ┌─▼──────────┐
              │ PostgreSQL│  │  Redis   │  │  Milvus    │
              │ 业务数据   │  │  任务队列 │  │  向量索引   │
              └───────────┘  └────┬─────┘  └────────────┘
                                  │
                          ┌───────▼────────┐
                          │ worker (独立)  │  摄取 / 重嵌入 / 重建索引
                          └────────────────┘
```

## 快速开始

### 一条命令（生产组合）

```bash
cp deploy/.env.example deploy/.env     # 填 LLM 与嵌入端点
docker compose -f deploy/docker-compose.prod.yml up -d --build
# 打开 http://localhost ，默认账号 admin / admin123（登录后立刻改密码）
```

起完想确认整条链路真的通，跑编排冒烟（建库 → 摄入 → 检索 → 流式对话）：

```bash
bash deploy/smoke_compose.sh --no-build      # 加 --down 跑完停栈，--infra-only 只验基础设施
```

### 本地开发（不需要容器也能跑）

```bash
# 后端
cd server
python -m venv .venv && .venv/Scripts/activate      # Windows
pip install -r requirements.txt
cp .env.example .env                                 # 指向本地模型端点
uvicorn app.main:app --reload --port 8000
# Windows 也可以直接双击 server/start.bat（独立窗口常驻，关窗口即停）

# 前端（另一个终端）
cd web
npm install
npm run dev                                          # http://localhost:5173，/api 反代到 8000
```

`server/.env.example` 默认指向**生产组合**（postgres + milvus + redis），所以本地开发前先起依赖：

```bash
cd deploy && docker compose up -d       # pg 5433 / redis 6380 / milvus 19530
cd ../server && python tools/check_infra.py --deep
```

想完全零依赖跑（不装任何数据库，数据落 `server/data/` 的 json 文件），把 `.env` 里那三行换成
注释掉的 `json / memory / local` 即可。**切后端必须重启后端与 worker，并同步迁移数据**——
否则会出现"接口写 PG、后台任务写 SQLite"这种半套状态。

### 从开发后端切到生产组合

```bash
cd deploy && docker compose up -d                    # pg 5433 / redis 6380 / milvus 19530
cd ../server
python tools/check_infra.py --deep                   # 三件套连通性 + 真实读写
python tools/migrate_json_to_db.py --backend postgres # 迁移知识库/会话/配置域（幂等）
```

然后在 `server/.env` 打开：

```
APP_STORE_BACKEND=postgres
APP_VECTOR_BACKEND=milvus
APP_QUEUE_BACKEND=redis
```

队列需要独立进程消费：`python -m app.workers.worker --concurrency 2`。

批量导入的并行度由两者共同决定：一次导入按 `APP_IMPORT_BATCH_SHARD`（默认 50 个文件）
切成若干队列任务，worker 有多少槽就同时跑多少片。所以「导入一个大目录」的耗时约等于
`文件数 × 单篇耗时 ÷ 并发数`，想快就加 `--concurrency`（受嵌入端点承载能力限制，
本地单卡建议 2~4，压太高反而全是超时重试）。

## 配置

所有配置走环境变量（前缀 `APP_`），也可写在 `server/.env`。

| 变量 | 默认 | 说明 |
|---|---|---|
| `APP_STORE_BACKEND` | `json` | `json` / `sqlite` / `postgres` |
| `APP_SQLITE_PATH` | `data/einstein.db` | sqlite 文件位置 |
| `APP_POSTGRES_DSN` | `postgresql://einstein:einstein@127.0.0.1:5433/einstein` | PG 连接串 |
| `APP_VECTOR_BACKEND` | `memory` | `memory`（numpy）/ `milvus` |
| `APP_MILVUS_URI` | `http://127.0.0.1:19530` | Milvus 地址 |
| `APP_MILVUS_COLLECTION` | `einstein_chunks` | 集合名，换嵌入模型时换名或 `--drop` |
| `APP_QUEUE_BACKEND` | `local` | `local`（DB 表）/ `redis` |
| `APP_REDIS_URL` | `redis://127.0.0.1:6380/0` | Redis 连接串 |
| `APP_IMPORT_BATCH_MAX_FILES` | `0`（不限） | 单批导入文件数上限，公网部署建议设 `2000` |
| `APP_IMPORT_BATCH_MAX_MB` | `0`（不限） | 单批累计大小上限（MB），公网部署建议设 `4096` |
| `APP_IMPORT_BATCH_SHARD` | `50` | 每 N 个文件切一个队列任务，决定批量导入的并行粒度 |
| `APP_LOCAL_IMPORT_ROOTS` | 空（禁用） | 允许导入的服务端目录，逗号分隔绝对路径 |
| `APP_LLM_BASE_URL` | `http://localhost:8080/v1` | OpenAI 兼容端点 |
| `APP_LLM_MODEL` | `qwen3.6-27b` | 对话模型 |
| `APP_EMBEDDING_BASE_URL` | `http://localhost:8081/v1` | 嵌入端点 |
| `APP_EMBEDDING_MODEL` | `qwen3-embedding-8b` | 嵌入模型 |
| `APP_RETRIEVAL_MODE` | `hybrid` | `vector` / `lexical` / `hybrid` |
| `APP_RRF_K` | `60` | RRF 融合平滑常数 |
| `APP_RETRIEVAL_MIN_SCORE` | `0.35` | 相关性门限，低于它的命中丢弃（按嵌入模型标定） |
| `APP_RERANK_PROVIDER` | `auto` | `auto` / `none` / `http`（OpenAI 兼容 `/rerank`）/ `onnx`（本机 bge-reranker） |
| `APP_RERANK_ENDPOINT` | 空 | `http` 档的重排端点，如 `http://127.0.0.1:8080/v1/rerank` |
| `APP_KB_STALE_DAYS` | `30` | 网页来源文档超过这么多天没重抓就在列表里标"可能过期"，`0` 关闭 |
| `APP_OCR_PROVIDER` | `auto` | `auto`（配了模型走视觉，否则看 pytesseract）/ `vision` / `tesseract` / `off` |
| `APP_OCR_MODEL` | 空 | 支持图片输入的模型名（如 `qwen2.5-vl-7b`）；空 = 不启用视觉档 |
| `APP_OCR_BASE_URL` | 空 | 视觉模型端点，空 = 跟随 `APP_LLM_BASE_URL` |
| `APP_OCR_MAX_PAGES` | `20` | 扫描件最多 OCR 前 N 页，超出部分在正文里标注被截断 |
| `APP_ALERT_INTERVAL_MINUTES` | `10` | 后台告警评估间隔，`0` 只留手动检查（规则本身在「运营 → 告警」里配） |
| `APP_AGENT_CODE_EXEC` | `true` | 关掉后智能体不注册代码执行工具 |
| `APP_AGENT_ENGINE` | `deepagents` | `deepagents`（计划/子智能体/压缩）/ `react`（旧单循环回滚用） |
| `APP_DEBUG` | `false` | 生产务必保持 false（否则异常会吐堆栈） |
| `APP_GITHUB_TOKEN` | 空 | 技能市场浏览，提额度用 |

## 验收脚本

都在 `server/tools/`，可单独跑，也被 CI 引用：

| 脚本 | 覆盖 |
|---|---|
| `check_infra.py --deep` | PG / Redis / Milvus / LLM / 嵌入的真实读写 |
| `verify_store_backends.py` | 知识库域 JSON / SQLite / PG 三后端 36 条 |
| `verify_history_backends.py` | 会话域三后端 69 条 |
| `verify_state_backends.py` | 8 个配置域三后端 + 并发写 |
| `verify_vector_index.py` | memory / Milvus 检索一致性 |
| `verify_queue.py` | local / Redis 队列（含并发抢单、租约回收） |
| `verify_ingest.py` | 摄取全链路：格式 / 去重 / 版本 / 回滚 / 切片编辑 / 异步任务 / 批量导入 |
| `verify_contracts.py` | 每个协议的所有后端实现是否齐全（防「只在某个后端漏方法」） |
| `gen_api_doc.py --check` | `docs/API参考.md` 与路由是否同步 |
| `backup.py verify` | 备份能否真恢复：manifest 逐项核对、sqlite integrity_check、pg_dump 是否含数据 |
| `backup.py prune` | 保留策略：留 N 份 + 不超 N 天，`list` 带大小与"几天前" |
| `report_capacity.py` | 规模（库/文档/切片/向量）、磁盘占用、`--probe` 实测检索延迟 |
| `full_sweep.py --skip-live` | 91 条接口巡检（契约 + 安全边界） |
| `web/ui-sweep.cjs` | 10 个前端路由 58 条页面断言 |
| `web/kb-batch-check.cjs` | 目录导入 UI：选目录 → 预览 → 进度 → 落库 9 条断言 |
| `web/agent-plan-check.cjs` | 智能体 UI：deepagents 计划块渲染、工具步骤与产物卡片 |

编排层另有两个入口：

| 脚本 | 用途 |
|---|---|
| `deploy/smoke_compose.sh` | 起容器 → 健康检查 → 建库/摄入/检索/对话全链路冒烟 |
| `sdk/smoke_client.py` | 用 SDK 跑同样的业务路径，验证对外接口对第三方可用 |

## 目录

```
server/
  app/api/routes/   控制台与开放 API
  app/services/     领域服务（rag / agent / store / queue / vector / state ...）
  app/db/           Database 抽象、迁移、域模型
  app/workers/      队列 worker 进程
  tools/            验收、迁移、重建、备份脚本
web/                React 18 + TS + Vite + Tailwind v4
deploy/             docker-compose（开发 / 生产）、.env 模板、编排冒烟
docs/               设计、进度、测试报告、运维手册、API 参考
sdk/                Python 客户端（无第三方依赖）
```

## 排障

- **登录后所有接口 401**：前端代理没生效。开发下确认 `web/vite.config.ts` 的 `/api` 指向 8000；
  容器下确认 nginx 的 `proxy_pass http://backend:8000`。
- **回答不流式、一次性全出来**：nginx 少了 `proxy_buffering off`（`web/nginx.conf` 已配）。
- **入库报 503**：嵌入端点不可用。`python tools/check_infra.py --skip-llm` 单独验嵌入。
- **Milvus 检索为空**：集合可能是空建的。`python tools/rebuild_vector_index.py --drop` 回灌。
- **Windows 上 uvicorn 退出码 127**：端口被占用，先 `netstat -ano | findstr :8000` 杀掉旧进程。
- **`create_admin` 不存在**：管理员在首次启动时自动创建（admin / admin123），无需手动建。

更多运维细节见 [docs/运维手册.md](docs/运维手册.md)。
