# Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [0.8.0] - 2026-11-09

团队与运营。0.7.0 把闭环补齐之后，剩下的问题不是"能不能用"，而是"多人用起来
谁在花钱、谁在出错、这把钥匙归谁管"。这一版把模型、权限、账单、告警四件事
从"写死在 `.env` 里"变成"可配置、可审计、可追责"。

### 新增

- **模型注册表** `models` 表 + `/api/models`（增删改查 + `POST /{id}/test` 真发一次
  `max_tokens=8` 的补全）。解析顺序为 `指定 model_id → 空间默认 → .env`，
  未登记模型时行为与升级前完全一致——不预置任何行
- **按专家选模型**：`experts.model_id`，对话与智能体共用同一份解析（`services/models.py`
  的 `resolve()`），不再两处各写一遍 `base_url/model` 然后慢慢漂移
- **成本看板** `GET /api/obs/cost?days=7`：按天 / 模型 / 成员 / 入口四个视角，
  每桶含 `runs/errors/tokens_in/tokens_out/llm_calls/cost/avg_ms`。`run_traces.cost`
  在 `finish()` 时按当时单价定格——改单价不改历史账
- **告警** `alert_rules` + `alert_events`：四类规则（错误率 / 检索空手率 / 日花费 /
  模型探活失败），webhook 按域名自动选格式（企业微信 / 钉钉 / 飞书 / 通用 JSON），
  带冷却时间与投递留痕（`delivered=false` 在页面上标红）
- **文档源同步**：文档记 `source_url` / `fetched_at`，单篇与批量重抓、过期清单。
  `digest` 未变时只更新 `meta.fetched_at`、**不重嵌入**
- **库级授权** `kb_grants`：`kb_access.access()` 返回 `read|write|None`，
  知识库列表把"授权给我的私有库"并进来并带 `my_role`
- **任务模板** `task_templates` + `/api/templates` + 智能体页模板条：只存起步参数
  （prompt / 专家 / 项目 / 工具 / 知识库），不写死步骤

### 变更

- **知识库读写拆开**（行为变化）：原实现用 `owns()` 同时判读写，"工作区可见"等于
  顺带给全空间写权限。现在读宽写严——18 个写入口单独标 `write=True`，
  预置库（`owner_id=''`）对所有人只读，改内容需显式 write 授权
- 告警评估分两条入口：`evaluate(owner_id=None)` 供后台定时任务（不看归属），
  `evaluate(owner_id=当前用户)` 供界面手动触发（只看自己的规则）
- 新增配置 `APP_KB_STALE_DAYS`（默认 30）、`APP_ALERT_INTERVAL_MINUTES`（默认 10），
  后台 `alert_loop` 与手动检查共用同一份 `evaluate_all()`

### 修复

- **token 从来没进过轨迹**：`traces.note_tokens` 从定义起没有任何调用点，
  `run_traces` 的 token 永远是 0——成本看板会是一张空表。补了 `llm.py` 两处与
  `LlmTimer.__exit__` 一处
- **默认模型跨空间串台**：`resolve()` 的缓存键只有 `model_id`，A 空间解析出的默认
  模型会被 B 空间命中。键改为 `model_id or f"default:{workspace_id}"`，
  `workspace_id` 一路透传到 `chat/agent/agent_deep`
- **冷却时间戳写不进去**：`update_rule()` 有字段白名单，`last_fired_at` 被静默丢弃，
  等于冷却永不生效。新增独立的 `mark_fired()` 写路径——否则任何能调 PATCH 的人
  都能把冷却清零
- **`visibility` 列从未建过**：dataclass 有字段、判定逻辑也在，但 `TableState.write`
  按 `tables.py` 的列声明取值，多出来的字段被静默丢弃，重启即失效。迁移 015 补列

## [0.7.0] - 2026-11-09

把插头插上。这一版不新增能力，只补闭环——审计列出的问题里，绝大多数是
"代码在、开关没通、界面没接"：限流中间件读不到用户、`note_tokens` 没人调用、
断连转后台做完了但前端从不接回。所以逐条跑起来看现象，而不是读代码判断。

### 新增

- **知识库管理界面**：切片列表与编辑、删单篇、单篇重建、版本列表与回滚、失败重试，
  六个接口接上前端。传错文件终于能单独删，不用删整库重传、重付一次嵌入成本
- **三档可见性**：知识库 / 专家 / 技能统一为 `private`（默认）/ `workspace` / `shared`，
  下拉 + 徽标，共享需显式操作
- **检索可信**：重排默认 `auto`；评测支持负例集（`expect_miss`，只测检索层，
  不进生成环节）；报告分出正例 / 负例通过率
- **可信对话**：重新生成（旧答案留一版，且不把上一版喂给模型）、编辑后重发、
  赞踩采集原因
- **运行可见**：`run_traces` 表 + `/api/obs/traces`，前端刷新后自动接回仍在跑的任务

### 变更

- 审批默认覆盖 `code_run`（原先只有显式配置才拦）
- 知识库 / 专家 / 技能的列表接口改为按可见性过滤，默认私有

### 修复

- **限流实际等于全放行**：中间件排在鉴权之前，读不到 `request.state.user`；
  重排到鉴权之后。`quota_incr("requests")` 也补上了调用点——日请求配额原先
  只有 tokens 在累加，`requests` 是个空转开关
- **删库 / 删文档不级联**：`document_versions` 里的正文留在库里，重建时命中旧数据。
  两套仓储四处一起改
- **MCP 凭证是自写 XOR 流**：换为 `enc:v2`（Encrypt-then-MAC，HMAC-SHA256 截断
  16 字节），v1 密文仍可解；篡改不再静默。`APP_ENV=prod` 时拒绝以默认
  `secret_key` 启动

## [0.6.1] - 2026-11-09

短期记忆落盘。0.5.1 把审批迁到了框架的 `interrupt` + `checkpointer`，但检查点
只活在进程内存里——服务一重启，"批准后从断点续跑"就退化成"按批准重跑"，
已写出的文件和已花的 token 全部重做一遍。这次把检查点真正写到磁盘上。

### 新增

- **检查点落盘** `app/services/checkpoints.py`，四档由 `APP_AGENT_CHECKPOINT` 控制：
  `auto`（默认，跟主存储走）/ `sqlite` / `postgres` / `memory` / `off`。
  sqlite 落到 `data/checkpoints.db`（WAL 模式），postgres 落到 `APP_POSTGRES_DSN`
  指向的库（`checkpoint*` 四张表，由框架 `setup()` 建，与业务表不冲突）
- **跨重启续跑**：强杀进程后重启，同一 `thread_id` 的图状态还在，批准后
  从断点继续而不是重跑。验收 `tools/verify_checkpoint_restart.py` 分两段跑，
  中间真的杀进程（10 项断言，含"没有退化成按批准重跑"）
- **检查点 TTL 清理**：`APP_AGENT_CHECKPOINT_TTL_HOURS`（默认 72）过期线程自动
  删除，启动时扫一次 + 每小时一次，退出时关连接（`main.py` 的 lifespan）。
  判据用 checkpoint 自带的 `ts`——看文件时间等于永远删不掉东西
- **可观测性** `GET /api/obs/checkpoints`：返回档位、落点、TTL、是否已连上。
  排查"批准后为什么重跑了"先看这里，`connected=false` 说明没落盘
- **测试** `tests/test_checkpoints.py`（13 项）：档位解析、跨连接读回、线程隔离、
  降级链、TTL 清理。全套 pytest 从 38 项增至 51 项

### 变更

- `APP_AGENT_CHECKPOINT` 默认值从 `memory` 改为 `auto`。本地开发（`store_backend`
  是 json/sqlite）自动用 sqlite，生产 postgres 主库在 Linux 上用 postgres——
  不用记两处配置。**行为变化**：本地重启后不再丢检查点，续跑能接上
- 降级链改为 `postgres → sqlite → memory`（原来是直接掉到 memory）。内存档等于
  "什么都没落"，用户看不到任何异常，直到某天发现任务被重跑了

### 修复

- **Windows 上 postgres 档静默降级**：psycopg 的异步连接要求 selector 事件循环，
  而 uvicorn 在 Windows 上建的是 Proactor（`app.main` 在 loop 内导入，模块级改
  policy 已经来不及）。此前配了 `postgres` 会白等 15 秒连接池超时再掉到内存，
  日志刷一堆 `cannot use the ProactorEventLoop`。现在 `auto` 按平台直接选
  sqlite，显式配 `postgres` 立即失败并说明原因（Linux 不受影响，实测容器内正常）
- **`verify_isolation` 空环境误报**：原来断言"库里必须存在预置专家"，空环境下
  直接 FAIL。改为用"A 也看不到"来区分环境缺数据（SKIP）与隔离真的坏了（FAIL）

## [0.6.0] - 2026-11-09

批次 B/C 的剩余项：让"看"和"跑"脱钩（流式 + 后台执行），把智能体接上长期
记忆，给向量加上可校验的身份，容器收到非 root + 只读根，验收脚本进 pytest。

### 新增

- **智能体真流式**：`astream(stream_mode=["updates","messages"])` 订阅模型增量，
  逐 token 转成 `delta` 事件。首字延迟从"全文生成完"降到"第一个 token"
  （实测 600 字回答：首帧 1.7s / 完成 7.2s，636 帧）。工具调用轮次里模型先说
  的那段话用 `answer_reset` 撤回，不会掺进最终答案
- **运行与 SSE 解耦**：执行放进独立 task，事件写进 `services/run_registry.py`，
  SSE 只是读者。客户端断开（切标签页、网络闪断）不再掐掉任务，跑完照样落库；
  `GET /agent/runs/{run_id}/stream` 可从已有事件重连接回（归属不符 404）
- **长期记忆接入智能体**：装配前按本轮任务召回工作区事实，拼进系统提示
  （`recall_memory` / `memory_block`）。deep 与 react 两个引擎行为一致；记忆
  读失败只降级为"没有背景"，不影响任务
- **浏览器进程复用**：截图不再每次冷启动 Chromium。按线程缓存（Playwright
  同步 API 绑定创建线程），空闲 `APP_BROWSER_IDLE_SECONDS`（默认 120s）回收，
  进程退出时清理；容器里补 `--disable-dev-shm-usage`
- **切片记嵌入指纹**：`chunks` 表加 `embedding_model` / `embedding_dim`（迁移 012）。
  检索前比对当前模型，不一致直接给出可操作提示而不是返回空；`retrieval.Retrieval`
  新增 `error` 字段，与"未命中"分开——前者要去重建索引，后者要补文档
- **重新嵌入**：`tools/rebuild_vector_index.py --reembed --drop`。换模型后光重建
  索引没用（旧向量还在旧空间里），必须重算向量；不重切片，保留边界与父子关系
- **pytest 测试体系**：`server/tests/`（38 项）+ `pytest.ini` + `requirements-dev.txt`，
  CI 增加"单元测试"步骤。测试自己把存储按到 sqlite 临时库，不碰 `.env` 里的
  生产后端

### 变更

- **容器收紧**：`--read-only` + `--tmpfs /tmp,/run` + `--user 1000:1000` +
  `PIP_USER=1`（依赖落到工作区，装包仍可用）。实测容器内 `id -u = 1000`、
  `touch /etc/x` 被拒、`pip install six` 成功。宿主目录属主与 uid 不匹配时
  自动降级为 root 重试一次并打日志（Linux bind mount 的现实约束）
- 三个配置项：`APP_AGENT_STREAM`、`APP_BROWSER_IDLE_SECONDS`、
  `APP_AGENT_PRO_RUN_AS` / `APP_AGENT_PRO_READONLY_ROOT`
- `screenshot` 的页面错误不再连带杀掉浏览器：`new_page` 失败时重建一次

### 修复

- **`_safe()` 把 `ApprovalRequired` 当普通异常吞掉**：react 引擎的审批暂停
  永远触发不了，用户看到的是工具返回一个 `{"ok": false}`，模型自己换路走。
  人工确认在回滚引擎上形同虚设——由新增的 pytest 用例发现
- **审批的"一次性"没落实**：`granted_tools` / `latest_decision` 没过滤
  `consumed`，一次批准在同一会话内持续生效，又变回"批一次永久放行"
- **检索失败伪装成"知识库未命中"**：向量与嵌入模型不匹配时返回空列表，
  用户会以为库里没内容而去重传文档

## [0.5.1] - 2026-11-09

按《评估报告-产品缺陷与改进》修掉批次 A 的 P0：**人工确认从"抛异常当暂停"
换成框架的 interrupt + checkpointer**，检索从"永远命中的 0.01 阈值"换成绝对
相关性门限。行为上最直观的差别是——批准之后从断点继续，而不是把任务从头再
跑一遍。

### 新增

- **人工确认落库** `app/services/approvals.py` + 迁移 011（`agent_approvals` 表）：
  键为 `(workspace, owner, session, tool, args_hash)`，`decision` 记
  approve/reject，`consumed` 保证一次确认只用一次，`expires_at` 默认 30 分钟。
  归属按 `(workspace, owner)` 过滤，别人的 session 查不到任何记录（统一 404，
  不区分"不存在"与"不是你的"）
- **参数指纹** `args_fingerprint(args)`：批准只对**这一组参数**有效。同一工具
  换参数要重新确认，避免"批了一次就等于永久放行这个工具"
- **检索门面** `app/services/retrieval.py`：`retrieve()` 一处收口改写、父片展开、
  门限、文档配额与引用；`/chat` 与 `/agent/run` 共用，两条入口的检索行为不再
  各写一套
- **引用透传**：智能体收尾帧带 `citations`，SSE 新增 `citations` 事件，前端
  在答案下方渲染「参考资料」（编号 / 文档名 / 相关度）
- **验收**：`tools/verify_p0_fixes.py`（34 项，脚本化模型驱动图，验证框架语义）、
  `tools/verify_hitl_e2e.py`（15 项，真实 HTTP + 真实模型验证"暂停→批准→续跑"
  与"拒绝后工具不执行"）

### 变更

- **审批改走 LangGraph interrupt**：`create_deep_agent(interrupt_on=..., checkpointer=...)`，
  中断在 `updates` 流里以 `__interrupt__` 键出现（不是异常）；批准后发
  `Command(resume={"decisions": [...]})` 到同一 `thread_id`（`{ws}:{session}`）。
  批量中断时 `decisions` 个数按 `aget_state().tasks[].interrupts` 对齐，模型一轮
  调两次敏感工具也能正确续跑
- **新增运行状态持久化** `agent_runtime.get_checkpointer()`：`memory`（进程内
  `InMemorySaver`，支持中断续跑，重启后失效）/ `off`（旧行为）。检查点丢失时
  引擎明确告知并退回「按批准重跑」，不假装续跑
- **相关性门限语义修正**：删掉 `MIN_SIMILARITY = 0.01`（任何文本都有 0.2 以上
  相似度，等于没设），改按**绝对分**判定（重排分优先，余弦其次，纯词法命中按
  jieba 词覆盖率 ≥0.5 放行）。候选池先用 `floor * 0.6` 粗剪，排序后用
  `apply_score_gate` 精判；全部被砍时打印最高分，便于按实际嵌入模型标定
- **同文档配额** `cap_per_document(hits, max_per_doc=2)`：一篇结构重复的文档
  不再能占满全部名额，四片全来自同一文件时"命中"其实是假象
- **`execute` 装配期裁剪**：`FilesystemMiddleware(backend=..., tools=[...])`
  按 `supports_execution(backend)` 决定挂不挂 `execute`，做到「工具集 ⊆ 后端
  能力集」。默认引擎不再给模型一个调用即报错的工具；Pro 引擎后端支持执行，
  照常挂上
- **嵌入分批**：`_split_batches(texts, max_items, max_chars)` 双阈值切分，
  `APP_EMBEDDING_BATCH_SIZE=32` / `APP_EMBEDDING_BATCH_CHARS=40000`，整篇长
  文档不再塞进一个请求等超时
- **容器网络默认 `none`**：`APP_AGENT_PRO_NETWORK` 由 `bridge` 改 `none`，需要
  装包时临时改回 bridge
- **项目 slug 空值拒绝**：拆出 `slug_from_name()`（可能为空）与 `slugify()`
  （回落 `untitled`，仅内部路径用）。全中文标点或纯符号的项目名不再静默撞进
  `untitled` 目录

### 修复

- **批准后重跑整个任务**：旧实现用抛异常中断，批准后前端只能把原任务重发一遍，
  已写的文件被再写一遍、已完成的步骤全部重做
- **审批状态存进程内存**：重启即丢、无归属校验（拿到 session_id 就能预批准）、
  永不过期、批一次永久放行
- **"知识库未命中"分支不可达**：阈值形同虚设，不相关片段被当成证据喂给模型
- **Token 日配额只记不拦**：`/chat` 与 `/agent/run` 入口补 `quota_check(ws, "tokens")`
  → 429，`APP_QUOTA_DAILY_TOKENS` 才真正生效
- **向量维度不符静默丢弃**：内存后端遇到维度不匹配时整批过滤且不报错，表现为
  "知识库有内容但检索永远为空"；改为打印丢弃条数与重建索引的命令
- **失败运行不留记录**：`/agent/run` 异常分支补 `status="failed"` 落库，产物已
  写盘却查不到运行记录的情况不再出现

## [0.5.0] - 2026-11-09

在 deepagents 之上加一个带真沙箱的 Pro 引擎，并把三个引擎里"自己实现"的部分
收敛回框架能力（本次以整理为主，对外事件协议不变）。

### 新增

- **Pro 引擎（`APP_AGENT_ENGINE=deepagents-pro`）**：`app/services/agent_deep_pro.py`
  - `ProBackend` 多继承 `SandboxBackendProtocol`，harness 检测到后端支持 execute 就多给模型一个 `execute` 工具；文件边界仍复用 `WorkspaceBackend`
  - 容器模式 `docker run --rm`：项目目录挂 `/work`，`--cap-drop ALL` / `no-new-privileges` / 内存 CPU PID 全限，超时补刀 `docker rm -f`；命令用 `sh -s` 从 stdin 读脚本，绕开 `sh -lc "..."` 的双层引号解析
  - 宿主降级走框架 `LocalShellBackend`（无隔离，结果里带提示）；`APP_AGENT_PRO_SANDBOX=container|local|off` 可控
  - 浏览器工具 `browser_open` / `browser_links` / `browser_shot`，schema 由 `@tool` 从签名生成；截图双后端（Python playwright → 仓库内 playwright-core）
- **公共运行时 `app/services/agent_runtime.py`**：`short` / `build_messages` / `LlmTimer` / `final_frame`，三个引擎共用同一套消息拼装、指标埋点与收尾帧
- **验收**：`tools/verify_deep_pro.py` 27 项（沙箱模式、项目边界、产物登记、浏览器、假模型端到端确认真实执行 execute）

### 变更

- deep 系两个引擎收敛到唯一入口 `_run_deep`：技能同步、装配、消息拼装、执行循环只有一份实现，Pro 只提供"装配回调 + 开场自述"
- ReAct 回滚路径迁到 `langchain.agents.create_agent`（`langgraph.prebuilt.create_react_agent` 已废弃），事件映射不再依赖节点名，只按消息类型判断，并按 id 去重
- 容器执行输出只回原始 stdout/stderr 与退出码，`[Command succeeded/failed with exit code N]` 与截断提示交给 harness，避免两条状态行
- 危险命令正则黑名单只在宿主 shell 模式生效：容器有真隔离，黑名单只会误杀；`APP_AGENT_PRO_NETWORK` 默认仍为 `bridge`（改 `none` 待定）
- 侧栏与登录页的版本号改由 `package.json` 经 Vite `define` 注入（`__APP_VERSION__`），界面不再手写版本串

### 修复

- deep 引擎改名为公开的 `WorkspaceBackend`（Pro 需要继承），`tools/verify_project_isolation.py` 同步

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
