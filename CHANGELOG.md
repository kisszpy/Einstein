# Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [0.3.0] - 2026-11-09

对应第二阶段批次 S2.3 / S2.4 / S2.5 / S2.6，RAG 质量平台化体验生态收官。

### 新增

- **检索质量（S2.3）**：查询改写（LLM 改写 + 超时回退 + 缓存）；Rerank 三级提供方（none / OpenAI 兼容 http / ONNX，失败自动降级原序）；父子切片（大父片拼接回原文，`parent_expand` 开关）；引用原文定位（字符区间 + 前后 200 字摘录，前端点引用展开）；评测裁判指标 context_precision / faithfulness；评测运行历史与 Δ 对比；检索多模式对比测试台
- **平台化（S2.4）**：viewer 只读角色（写路径中间件拦截）；令牌桶限流（登录/对话/智能体/检索/开放 API 分级，Redis 固定窗口 + 内存兜底）；工作区日配额（请求数 / token）；邀请码加入工作区；审计筛选与 CSV 导出；shared 可见性跨工作区共享知识库
- **对话与智能体体验（S2.5）**：会话重命名 / 置顶 / 归档 / 全文搜索（标题+正文）；回答赞踩反馈；长期记忆（手动管理 + 相关记忆自动注入系统提示）；模型降级链（`base_url::model::api_key;...` 依次切换）；敏感工具人工确认（暂停 → 批准条 → 自动续跑）
- **技能/专家/MCP 完善（S2.6）**：技能 semver 版本管理与一键回滚；技能 zip 导入导出（防炸弹 + 路径穿越防护）；技能沙箱试运行；专家开场白 / 推荐问题 / 发布流（draft→testing→live，live 才进对话页）；MCP 凭证加密落盘（SHA-256 密钥流，掩码回显）；MCP 工具审批白名单；MCP 调用并发隔离（单服务器上限 4）与审计留痕

### 修复

- 会话全文搜索 SQL 括号未闭合导致 500（`EXISTS(...)` 外层括号缺失）
- PG 读路径失败不回滚，事务停留在 aborted 状态毒化后续所有请求
- 技能导出文件名含中文导致 latin-1 header 编码 500（改用 RFC 5987 filename*）
- 会话 PATCH 重命名与置顶/归档互斥，无法同时生效

## [0.2.0] - 2026-11-09

对应第二阶段批次 S2.0 / S2.1 / S2.2 / S2.7 及智能体引擎升级。

### 新增

- **存储层生产组合**：PostgreSQL(5433) / Redis(6380) / Milvus(19530) 真实接线，默认启用；保留 json/sqlite/memory 后端可切换（`APP_STORE_BACKEND` / `APP_VECTOR_BACKEND` / `APP_QUEUE_BACKEND`），Repository 双后端同契约（100 项静态对齐检查）
- **RAG 写路径规模化**：PDF / Word / Excel / PPT / CSV / HTML / Markdown 多格式解析；异步摄取任务（解析→去重→切片→嵌入→入库，进度可查）；sha256 文档去重；同文档多版本与回滚；切片预览与编辑（单条重嵌入）
- **整目录导入**：前端选文件夹 / 拖拽目录（保留相对路径作文档名）/ ZIP 自动解包 / 服务端目录白名单导入；单批文件数与大小默认不限（`APP_IMPORT_BATCH_MAX_FILES/MB=0`），按 `APP_IMPORT_BATCH_SHARD`（默认 50）分片并行消费
- **智能体引擎升级**：默认切换 Deep Agents harness（`deepagents 0.7.34`）——显式计划（write_todos→前端计划卡）、上下文摘要压缩、task 子智能体；文件落盘走 FilesystemBackend（根=工作区），产物卡片/预览/下载不变；`APP_AGENT_ENGINE=react` 可回滚旧引擎
- **KB 优先检索**：挂载知识库时首轮强制预取检索注入上下文 + 系统指令硬规则（KB 第一信息源、查不到才 web_search、注明文档名）
- **交付工程**：Dockerfile（server 多阶段 / web+nginx）、`docker-compose.prod.yml`、GitHub Actions CI（ruff/tsc/契约检查/API 文档防漂移）、`docs/API参考.md`（95 操作，OpenAPI 生成）、Python SDK（chat/search/agent/kb）、备份脚本 `backup.py` / `restore.py`

### 修复

- worker 空闲时不再静默退出（redis-py ≥6 的 BLPOP 超时异常）；槽线程异常兜底，防"槽静默消失→进程 exit 0"
- 在途任务不再往已删除的知识库写数据（消费前校验，孤儿文档/切片清零）
- 批量暂存目录按 TTL 兜底清理（`APP_IMPORT_STAGING_TTL_HOURS`，默认 6h）+ 启动回收；handler 只删自己的文件并如实上报删不掉的
- 切片 id 撞号（删文档后新切片与旧切片 id 重复导致检索串内容）
- 检索热路径：BM25 分词按 chunk_id 缓存；`list_chunks(with_vector=False)` 不再把 4096 维向量读进内存——hybrid p50 1196ms→99ms，lexical 1257ms→57ms
- 路径安全：zip 穿越防护、符号链接拒绝、服务端目录白名单 resolve 比较

### 已知问题

- 项目名含非法字符时被静默改名而非拒绝（P2）
- 引用溯源止于文档名+切片文本，未定位到原文段落（S2.3）
- 单租户：无多租户隔离、限流与配额（S2.4）；初始账号 `admin/admin123`，**部署后必须立即改密码**，公网部署前建议自行加登录限流

### 升级说明

从 v0.1.0 升级：`server/tools/migrate_json_to_db.py` 幂等迁移 JSON 数据到 PostgreSQL；`rebuild_vector_index.py` 回灌 Milvus；详见 `docs/运维手册.md`。

## [0.1.0] - 2026-11-05

- RAG 问答闭环：摄取管道 → 混合检索（向量+BM25+RRF）→ 流式回答 + 引用卡片
- 智能体：LangGraph ReAct、内置工具 + MCP 工具注册表、步骤流可视化、多轮会话
- 技能 / 专家 / MCP 管理、项目沙盒（文件树、预览、PPT 12 版式）
- 平台：工作区、API Key、审计、指标、评测集、开放 API（`/v1/chat/completions` 等）
