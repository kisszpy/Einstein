# Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [0.4.0] - 2026-11-09

多租户边界从「空间级」下沉到「人」：同一空间内的成员不再互相看见对方的资源，
工作区也不再漏出平台自身的运行时目录。

### 新增

- **资源级用户隔离**：`owner_id` 贯穿 `conversations` / `agent_runs` / `knowledge_bases` / `experts` / `skills` / `mcp_servers` / `api_keys` 七张表（迁移 009，幂等加列 + 复合索引）。语义为「预置共享 + 用户私有」——`''` 表示系统预置（全员可见、仅管理员可改），非空表示创建者（仅本人可见，管理员亦不越权，与个人空间边界一致）
  - 双维度收敛 `owned_kb_ids(ws, ids, uid)` / `visible(id, ws, uid)`：workspace 防跨空间、owner 防同空间越权
  - 可读与可改分离（`can_edit(item, uid, is_admin)`）；越权探测统一 404 不泄露存在性，预置资源改不动返回 403
  - 技能 id 混入归属（`_skill_id(ws, repo, subpath, owner_id)`），同空间两人装同一 GitHub 技能不再互相覆盖；工作区技能目录只发布「预置 + 本人」
  - API Key 继承签发者范围，开放 API 按 `key.owner_id` 收敛知识库与专家，不会因管理员签发而获得全库通行
  - 对话续写收到他人 `conversation_id` 时静默新开会话；MCP 工具按 `visible_server` 收敛
- **项目先登记再创建**：新增 `projects` 表（迁移 010，`workspace_id + slug` 复合主键，含 `owner_id` / `internal`）。列表只认「已登记 + 属于当前用户」的行，磁盘目录降级为落点；项目没有「预置」档，每个项目都属于某个人，空间管理员也不越权，越权一律 404
- **运行时目录迁出用户命名空间**：技能同步目录 `skills/` → `.agent/skills/`，临时沙盒 `_scratch` → 按用户隔离的 `_scratch-<uid>`；模型侧根目录 `ls` 只显示活动项目，`/.agent/skills/` 仍可读（渐进披露不变），写 `.agent/**` 一律拒绝。文件树与整包下载统一跳过 `.agent` / `.deck` / `.deckpreview` / `.slidep`
- **租户数据一键清理**：`tools/reset_tenant_data.py`（`--dry-run` / `--yes`），删前整库 `pg_dump` + 文件层 tar 备份；清 17 张业务表与非 admin 账号/空间，保留预置技能与专家
- **验收脚本**：`tools/verify_isolation.py`（两账号交叉验证六类资源 28 项）、`tools/verify_projects_scope.py`（项目可见性 22 项）

### 修复

- 系统管理页「新增成员」按钮被说明文字挤到 60px 导致四字折行溢出：文字块 `min-w-0 flex-1`、按钮 `shrink-0 whitespace-nowrap`，成员表改通栏展示
- 迁移前存量资源 `owner_id` 为空等于全员共享，清理后统一回填为创建者
- 删除账号时未连带清理其个人空间，留下孤儿空间与成员关系
- `verify_agent` 在 deep 引擎下的旧工具名断言（`fs_write` 已被 harness 工具取代）
- `verify_rbac_http` 清理逻辑遗漏探测用户的个人空间，残留测试数据

### 变更

- `skills`、`_` 前缀列为项目保留名，创建直接 400
- 启动自愈 `projects.adopt_orphans()`：把升级前的存量目录收养登记给空间所有者（宁缺勿泄），跳过运行时目录

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
