# Einstein API 参考

> 本文由 `server/tools/gen_api_doc.py` 从应用自身的 OpenAPI schema 生成，**请勿手工编辑**。
> 改完路由后运行 `python tools/gen_api_doc.py` 重新生成；CI 用 `--check` 校验是否漂移。
>
> 接口数：95 个操作 / 76 个路径 · 应用版本 0.1.0

## 1. 通用约定

### 1.1 基础地址

默认 `http://127.0.0.1:8000`。前端开发态由 vite 把 `/api` 与 `/v1` 反代到该地址，
生产由 nginx 反代（见 `web/nginx.conf`）。

### 1.2 鉴权

服务有两套互不干扰的凭证，都走 `Authorization: Bearer`：

| 场景 | 凭证 | 获取方式 | 说明 |
| --- | --- | --- | --- |
| 控制台 `/api/*` | 会话令牌 | `POST /api/auth/login` | 默认 12 小时过期；退出登录即失效 |
| 开放 API `/v1/*` | `sk-` 开头的 API Key | 控制台签发（`POST /api/apikeys`） | 按 scope 授权，只存哈希，明文仅签发时返回一次 |

免鉴权端点只有两个：`GET /api/health`、`POST /api/auth/login`。

```bash
# 控制台
TOKEN=$(curl -s --noproxy '*' -X POST http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"***"}' | python -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -s --noproxy '*' http://127.0.0.1:8000/api/kb -H "Authorization: Bearer $TOKEN"

# 开放 API
curl -s --noproxy '*' http://127.0.0.1:8000/v1/search \
  -H "Authorization: Bearer sk-xxxx" -H 'Content-Type: application/json' \
  -d '{"query":"报销流程","top_k":5}'
```

### 1.3 工作区

多租户隔离靠请求头 `x-workspace: <workspace_id>`。不传则落到默认工作区；
传了用户无权访问的工作区返回 403。管理员的 `GET /api/workspaces` 返回全部工作区。

### 1.4 错误格式

失败响应统一为 `{"detail": "<可直接展示给用户的说明>"}`：

| 状态码 | 含义 |
| --- | --- |
| 400 | 参数不合法（格式不支持、URL 拉取失败、文件过大等） |
| 401 | 未登录 / 令牌过期 / Key 无效 |
| 403 | 无权限（非管理员、工作区越界、Key 缺 scope） |
| 404 | 资源不存在 |
| 409 | 冲突（重名、状态不允许） |
| 422 | 请求体结构不符合 schema（FastAPI 校验） |
| 500 | 服务端异常；详情只进日志，不回传内部堆栈 |

### 1.5 数据格式

- 时间字段均为 ISO-8601 UTC 字符串，精确到秒，如 `2026-11-07T05:00:30+00:00`。
- 列表接口默认不分页，返回数组；`/api/audit` 与 `/api/jobs` 支持 `limit`。

### 1.6 SSE 流式协议（`POST /api/chat`）

响应为 `text/event-stream`，逐帧 `event: <name>\ndata: <json>\n\n`。事件顺序：

| 事件 | 时机 | 关键字段 |
| --- | --- | --- |
| `meta` | 立即 | `conversation_id` |
| `web` | 联网判定/检索过程，可多次 | `status`：`checking` → `searching` → `done` / `skipped` / `failed`；附带 `query`、`reason`、`message` |
| `citations` | 命中知识库时，一次 | `citations[]`：`index`、`document_name`、`chunk_id`、`text`、`score` |
| `reasoning` | 模型思考过程（若开启） | `text` |
| `delta` | 正文增量，多帧 | `text`，按序拼接即完整回答 |
| `done` | 正常结束 | `conversation_id` |
| `error` | 异常结束 | `message` |

生产环境反代必须关闭缓冲，否则整段回答会攒到最后一次性吐出：
nginx 需 `proxy_buffering off;`，并放宽 `proxy_read_timeout`。
服务端每 15 秒没有事件会发一帧注释 `: ping` 保活，客户端应忽略以 `:` 开头的行。

## 2. 接口索引

| 方法 | 路径 | 说明 | 鉴权 |
| --- | --- | --- | --- |

| `GET` | `/api/health` | 健康检查 | 公开 |
| `POST` | `/api/auth/login` | 登录，返回会话令牌 | 公开 |
| `POST` | `/api/auth/logout` | 退出登录，令牌立即失效 | 会话 |
| `GET` | `/api/auth/me` | 当前登录用户 | 会话 |
| `GET` | `/api/users` | 用户列表 | 会话 · 管理员 |
| `POST` | `/api/users` | 新建用户 | 会话 · 管理员 |
| `PATCH` | `/api/users/{user_id}` | 更新用户（昵称 / 角色 / 工作区 / 启停） | 会话 · 管理员 |
| `DELETE` | `/api/users/{user_id}` | 删除用户 | 会话 · 管理员 |
| `POST` | `/api/users/{user_id}/password` | 重置密码 | 会话 · 管理员 |
| `POST` | `/api/chat` | SSE 流式对话。 | 会话 |
| `GET` | `/api/conversations` | 会话列表 | 会话 |
| `GET` | `/api/conversations/{conversation_id}` | 会话详情（含全部消息） | 会话 |
| `DELETE` | `/api/conversations/{conversation_id}` | 删除会话 | 会话 |
| `POST` | `/api/agent/run` | SSE 运行智能体。事件：meta -> (step|artifact)* -> answ… | 会话 |
| `GET` | `/api/agent/runs` | 智能体运行记录 | 会话 |
| `DELETE` | `/api/agent/runs/{run_id}` | 删除一个任务会话（含所有追问轮次）。参数可以是会话 id 或任一轮次的 run id。 | 会话 |
| `GET` | `/api/agent/tools` | 可用工具清单 | 会话 |
| `GET` | `/api/kb` | 知识库列表 | 会话 |
| `POST` | `/api/kb` | 新建知识库 | 会话 |
| `POST` | `/api/kb/search` | 检索测试台：直接看召回效果，可切换检索模式对比。 | 会话 |
| `DELETE` | `/api/kb/{kb_id}` | 删除知识库，连同其文档、切片与向量一并清理。 | 会话 |
| `PATCH` | `/api/kb/{kb_id}/chunks/{chunk_id}` | 改切片文本并重算向量，改完立刻可被检索命中。 | 会话 |
| `GET` | `/api/kb/{kb_id}/documents` | 文档列表（含解析状态与切片数） | 会话 |
| `POST` | `/api/kb/{kb_id}/documents` | 粘贴文本入库（同步）。 | 会话 |
| `POST` | `/api/kb/{kb_id}/documents/async` | 上传即返回：文件落盘 + 入队，解析/切片/嵌入全部交给 worker。 | 会话 |
| `POST` | `/api/kb/{kb_id}/documents/batch` | 批量导入：一次收一个目录（多文件），或一个 ZIP 包。 | 会话 |
| `POST` | `/api/kb/{kb_id}/documents/dir` | 导入服务端目录（本地部署用）：不复制文件，直接按相对路径入库。 | 会话 |
| `POST` | `/api/kb/{kb_id}/documents/upload` | 上传并同步解析入库（小文件用；大文件走 /documents/async）。 | 会话 |
| `POST` | `/api/kb/{kb_id}/documents/url` | 抓网页入库（同步，限制 8MB）。 | 会话 |
| `DELETE` | `/api/kb/{kb_id}/documents/{doc_id}` | 删文档并级联清理切片与向量。 | 会话 |
| `GET` | `/api/kb/{kb_id}/documents/{doc_id}/chunks` | 切片预览：前端切片编辑器与引用定位都靠它。 | 会话 |
| `POST` | `/api/kb/{kb_id}/documents/{doc_id}/reindex` | 按当前切片策略重建这一篇（版本号不变）。 | 会话 |
| `POST` | `/api/kb/{kb_id}/documents/{doc_id}/rollback` | 回滚到指定版本：把那份内容作为新版本重新入库（历史不丢）。 | 会话 |
| `GET` | `/api/kb/{kb_id}/documents/{doc_id}/versions` | 文档历史版本 | 会话 |
| `GET` | `/api/projects` | 项目列表 | 会话 |
| `POST` | `/api/projects` | 新建项目 | 会话 |
| `DELETE` | `/api/projects/{project}` | 删除项目 | 会话 |
| `GET` | `/api/projects/{project}/archive` | 打包下载整个项目 | 会话 |
| `GET` | `/api/projects/{project}/deck` | 读取 PPT 结构化数据 | 会话 |
| `PUT` | `/api/projects/{project}/deck` | 保存 PPT 结构化数据 | 会话 |
| `GET` | `/api/projects/{project}/download` | 下载单个文件 | 会话 |
| `GET` | `/api/projects/{project}/file` | 读取单个文件 | 会话 |
| `PUT` | `/api/projects/{project}/file` | 写入单个文件 | 会话 |
| `GET` | `/api/projects/{project}/file/{ticket}/{file_path}` | 静态资源与站内跳转的落点（ticket 由中间件校验，这里只是路径占位）。 | 会话 |
| `GET` | `/api/projects/{project}/preview` | 预览入口：HTML 注入 base 后渲染（沙箱隔离），其他类型按 MIME 内联返回。 | 会话 |
| `GET` | `/api/projects/{project}/tree` | 项目目录树 | 会话 |
| `GET` | `/api/experts` | 专家列表 | 会话 |
| `POST` | `/api/experts` | 新建专家 | 会话 |
| `GET` | `/api/experts/{expert_id}` | 专家详情 | 会话 |
| `PUT` | `/api/experts/{expert_id}` | 更新专家 | 会话 |
| `DELETE` | `/api/experts/{expert_id}` | 删除专家 | 会话 |
| `GET` | `/api/skills` | 技能列表 | 会话 |
| `POST` | `/api/skills` | 新建技能 | 会话 |
| `POST` | `/api/skills/install` | 安装技能 | 会话 |
| `POST` | `/api/skills/install/preview` | 安装预检：先看会写入哪些文件 | 会话 |
| `GET` | `/api/skills/market` | 技能市场 | 会话 |
| `PUT` | `/api/skills/{skill_id}` | 更新技能 | 会话 |
| `DELETE` | `/api/skills/{skill_id}` | 删除技能 | 会话 |
| `GET` | `/api/skills/{skill_id}/file` | 读取技能内单个文件 | 会话 |
| `GET` | `/api/skills/{skill_id}/files` | 技能文件清单 | 会话 |
| `POST` | `/api/skills/{skill_id}/update` | 从来源重新拉取技能 | 会话 |
| `GET` | `/api/mcp/servers` | MCP 服务列表 | 会话 |
| `POST` | `/api/mcp/servers` | 接入 MCP 服务 | 会话 |
| `PATCH` | `/api/mcp/servers/{server_id}` | 更新 MCP 服务 | 会话 |
| `DELETE` | `/api/mcp/servers/{server_id}` | 移除 MCP 服务 | 会话 |
| `POST` | `/api/mcp/servers/{server_id}/call` | 直连调用一次工具（管理端调试用）。 | 会话 |
| `POST` | `/api/mcp/servers/{server_id}/refresh` | 重新拉取工具清单 | 会话 |
| `GET` | `/api/eval/sets` | 评测集列表 | 会话 |
| `POST` | `/api/eval/sets` | 新建评测集 | 会话 |
| `DELETE` | `/api/eval/sets/{set_id}` | 删除评测集 | 会话 |
| `POST` | `/api/eval/sets/{set_id}/run` | mode 可选：vector / lexical / hybrid，用于对比不同检索策略。 | 会话 |
| `GET` | `/api/jobs` | 后台任务列表 | 会话 |
| `GET` | `/api/jobs/stats` | 队列统计（各状态计数） | 会话 |
| `GET` | `/api/jobs/{job_id}` | 任务详情（含 progress） | 会话 |
| `POST` | `/api/jobs/{job_id}/retry` | 重试失败任务 | 会话 |
| `GET` | `/api/apikeys` | API Key 列表（不返回明文） | 会话 |
| `POST` | `/api/apikeys` | 签发 API Key，明文仅此一次返回 | 会话 |
| `DELETE` | `/api/apikeys/{key_id}` | 撤销 API Key | 会话 |
| `GET` | `/api/audit` | 审计日志 | 会话 |
| `GET` | `/api/workspaces` | 工作区列表按账号可见范围过滤：管理员看全部，成员只看归属。 | 会话 |
| `POST` | `/api/workspaces` | 新建工作区 | 会话 · 管理员 |
| `PATCH` | `/api/workspaces/{ws_id}` | 更新工作区 | 会话 · 管理员 |
| `DELETE` | `/api/workspaces/{ws_id}` | 只允许删除空工作区，避免误删带走数据。 | 会话 · 管理员 |
| `GET` | `/api/browse/asset` | 单资源代理：CSS/图片/脚本经服务端取回，绕开防盗链与混合内容。 | 会话 |
| `GET` | `/api/browse/read` | 抽取网页正文 | 会话 |
| `GET` | `/api/browse/themes` | PPT 编辑器的主题色板。放在 /browse 下是为了避开 /projects/{pro… | 会话 |
| `GET` | `/api/browse/ticket` | 给 iframe / img 用的短期票据（15 分钟，只放行预览类 GET）。 | 会话 |
| `GET` | `/api/browse/web` | 网页代理（只读） | 会话 |
| `DELETE` | `/api/obs/` | 清空指标 | 会话 |
| `GET` | `/api/obs/llm` | 模型调用统计 | 会话 |
| `GET` | `/api/obs/requests` | 请求耗时分布 | 会话 |
| `GET` | `/api/obs/summary` | 整体概览 | 会话 |
| `POST` | `/v1/agent/run` | 运行智能体 | Key · agent |
| `POST` | `/v1/chat/completions` | 对话补全（OpenAI 兼容，支持 stream） | Key · chat |
| `POST` | `/v1/search` | 检索知识库 | Key · search |

## 3. 接口详情

### 3.1 健康检查（`health`）

#### `GET /api/health`

健康检查

**鉴权**：公开

**响应**：`200`

### 3.2 登录与用户（`auth`）

#### `POST /api/auth/login`

登录，返回会话令牌

**鉴权**：公开

**响应**：`200`

#### `POST /api/auth/logout`

退出登录，令牌立即失效

**鉴权**：会话

**响应**：`200`

#### `GET /api/auth/me`

当前登录用户

**鉴权**：会话

**响应**：`200`

#### `GET /api/users`

用户列表

**鉴权**：会话 · 管理员

**响应**：`200`

#### `POST /api/users`

新建用户

**鉴权**：会话 · 管理员

**响应**：`200`

#### `PATCH /api/users/{user_id}`

更新用户（昵称 / 角色 / 工作区 / 启停）

**鉴权**：会话 · 管理员

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `user_id` | path | string | 是 | — |

**响应**：`200`

#### `DELETE /api/users/{user_id}`

删除用户

**鉴权**：会话 · 管理员

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `user_id` | path | string | 是 | — |

**响应**：`200`

#### `POST /api/users/{user_id}/password`

重置密码

**鉴权**：会话 · 管理员

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `user_id` | path | string | 是 | — |

**响应**：`200`

### 3.3 对话（SSE）（`chat`）

#### `POST /api/chat`

SSE 流式对话。

事件：meta -> web(状态) -> [citations] -> (reasoning|delta)* -> done | error
web 的 status 依次为 checking / searching / done / skipped / failed。

**鉴权**：会话

事件协议与保活约定见 [1.6 SSE 流式协议](#16-sse-流式协议post-apichat)。

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `x-workspace` | header | string? | 否 | — |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `conversation_id` | string? | 否 | — |
| `messages` | object[] | 是 | — |
| `expert_id` | string? | 否 | — |
| `kb_ids` | string[] | 否 | 默认 `[]` |
| `web_mode` | string | 否 | 默认 `auto` |

<details><summary>嵌套字段</summary>

`messages[]` 元素
| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `role` | string | 是 | — |
| `content` | string | 是 | — |

</details>

**响应**：`200`

### 3.4 会话历史（`conversations`）

#### `GET /api/conversations`

会话列表

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `GET /api/conversations/{conversation_id}`

会话详情（含全部消息）

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `conversation_id` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `DELETE /api/conversations/{conversation_id}`

删除会话

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `conversation_id` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

### 3.5 智能体（`agent`）

#### `POST /api/agent/run`

SSE 运行智能体。事件：meta -> (step|artifact)* -> answer_start -> delta* -> done | error。

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `x-workspace` | header | string? | 否 | — |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `task` | string | 是 | — |
| `tool_ids` | string[] | 否 | 默认 `[]` |
| `kb_ids` | string[] | 否 | 默认 `[]` |
| `project` | string | 否 | — |
| `session_id` | string | 否 | — |

**响应**：`200`

#### `GET /api/agent/runs`

智能体运行记录

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `DELETE /api/agent/runs/{run_id}`

删除一个任务会话（含所有追问轮次）。参数可以是会话 id 或任一轮次的 run id。

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `run_id` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `GET /api/agent/tools`

可用工具清单

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

### 3.6 知识库（`kb`）

#### `GET /api/kb`

知识库列表

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `POST /api/kb`

新建知识库

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `x-workspace` | header | string? | 否 | — |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 | — |

**响应**：`200`

#### `POST /api/kb/search`

检索测试台：直接看召回效果，可切换检索模式对比。

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `x-workspace` | header | string? | 否 | — |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `kb_ids` | string[] | 是 | — |
| `query` | string | 是 | — |
| `top_k` | integer | 否 | 默认 `4` |
| `mode` | string? | 否 | — |

**响应**：`200`

#### `DELETE /api/kb/{kb_id}`

删除知识库，连同其文档、切片与向量一并清理。

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `kb_id` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `PATCH /api/kb/{kb_id}/chunks/{chunk_id}`

改切片文本并重算向量，改完立刻可被检索命中。

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `kb_id` | path | string | 是 | — |
| `chunk_id` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `text` | string | 是 | — |

**响应**：`200`

#### `GET /api/kb/{kb_id}/documents`

文档列表（含解析状态与切片数）

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `kb_id` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `POST /api/kb/{kb_id}/documents`

粘贴文本入库（同步）。

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `kb_id` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 | — |
| `content` | string | 是 | — |
| `strategy` | string | 否 | 默认 `recursive` |
| `chunk_size` | integer | 否 | 默认 `500` |
| `chunk_overlap` | integer | 否 | 默认 `50` |

**响应**：`200`

#### `POST /api/kb/{kb_id}/documents/async`

上传即返回：文件落盘 + 入队，解析/切片/嵌入全部交给 worker。

50MB 的 PDF 也不会占住 HTTP worker；前端拿 job_id 轮询进度。

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `kb_id` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `POST /api/kb/{kb_id}/documents/batch`

批量导入：一次收一个目录（多文件），或一个 ZIP 包。

前端选文件夹时会把 `webkitRelativePath` 一起提交，"产品/需求.md" 就成了文档名，
同类文件不会因重名覆盖，检索结果也能看出出处。整批入一个任务，
进度用 GET /jobs/{id} 看 done/total 与失败清单。

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `kb_id` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `POST /api/kb/{kb_id}/documents/dir`

导入服务端目录（本地部署用）：不复制文件，直接按相对路径入库。

安全上默认关闭，需在 .env 配 `APP_LOCAL_IMPORT_ROOTS`（逗号分隔的绝对路径）；
未配置时返回 403 并说明怎么开。符号链接与依赖目录（node_modules/.git 等）会跳过。

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `kb_id` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `path` | string | 是 | — |
| `recursive` | boolean | 否 | 默认 `True` |
| `strategy` | string | 否 | 默认 `recursive` |
| `chunk_size` | integer | 否 | 默认 `500` |
| `chunk_overlap` | integer | 否 | 默认 `50` |

**响应**：`200`

#### `POST /api/kb/{kb_id}/documents/upload`

上传并同步解析入库（小文件用；大文件走 /documents/async）。

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `kb_id` | path | string | 是 | — |
| `strategy` | query | string | 否 | 默认 `recursive` |
| `chunk_size` | query | integer | 否 | 默认 `500` |
| `chunk_overlap` | query | integer | 否 | 默认 `50` |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `POST /api/kb/{kb_id}/documents/url`

抓网页入库（同步，限制 8MB）。

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `kb_id` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `url` | string | 是 | — |
| `name` | string? | 否 | — |

**响应**：`200`

#### `DELETE /api/kb/{kb_id}/documents/{doc_id}`

删文档并级联清理切片与向量。

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `kb_id` | path | string | 是 | — |
| `doc_id` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `GET /api/kb/{kb_id}/documents/{doc_id}/chunks`

切片预览：前端切片编辑器与引用定位都靠它。

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `kb_id` | path | string | 是 | — |
| `doc_id` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `POST /api/kb/{kb_id}/documents/{doc_id}/reindex`

按当前切片策略重建这一篇（版本号不变）。

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `kb_id` | path | string | 是 | — |
| `doc_id` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `POST /api/kb/{kb_id}/documents/{doc_id}/rollback`

回滚到指定版本：把那份内容作为新版本重新入库（历史不丢）。

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `kb_id` | path | string | 是 | — |
| `doc_id` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `version` | integer | 是 | — |

**响应**：`200`

#### `GET /api/kb/{kb_id}/documents/{doc_id}/versions`

文档历史版本

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `kb_id` | path | string | 是 | — |
| `doc_id` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

### 3.7 项目与文件（`projects`）

#### `GET /api/projects`

项目列表

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `POST /api/projects`

新建项目

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `x-workspace` | header | string? | 否 | — |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 | — |
| `template` | string | 否 | 默认 `blank` |
| `description` | string | 否 | — |

**响应**：`200`

#### `DELETE /api/projects/{project}`

删除项目

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `project` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `GET /api/projects/{project}/archive`

打包下载整个项目

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `project` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `GET /api/projects/{project}/deck`

读取 PPT 结构化数据

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `project` | path | string | 是 | — |
| `path` | query | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `PUT /api/projects/{project}/deck`

保存 PPT 结构化数据

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `project` | path | string | 是 | — |
| `path` | query | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `deck` | object | 是 | — |

**响应**：`200`

#### `GET /api/projects/{project}/download`

下载单个文件

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `project` | path | string | 是 | — |
| `path` | query | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `GET /api/projects/{project}/file`

读取单个文件

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `project` | path | string | 是 | — |
| `path` | query | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `PUT /api/projects/{project}/file`

写入单个文件

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `project` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `path` | string | 是 | — |
| `content` | string | 是 | — |

**响应**：`200`

#### `GET /api/projects/{project}/file/{ticket}/{file_path}`

静态资源与站内跳转的落点（ticket 由中间件校验，这里只是路径占位）。

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `project` | path | string | 是 | — |
| `ticket` | path | string | 是 | — |
| `file_path` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `GET /api/projects/{project}/preview`

预览入口：HTML 注入 base 后渲染（沙箱隔离），其他类型按 MIME 内联返回。

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `project` | path | string | 是 | — |
| `path` | query | string | 是 | — |
| `ticket` | query | string | 否 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `GET /api/projects/{project}/tree`

项目目录树

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `project` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

### 3.8 专家（`experts`）

#### `GET /api/experts`

专家列表

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `POST /api/experts`

新建专家

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `x-workspace` | header | string? | 否 | — |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 | — |
| `description` | string | 否 | — |
| `emoji` | string | 否 | 默认 `🤖` |
| `persona` | string | 否 | — |
| `skill_ids` | string[] | 否 | 默认 `[]` |
| `kb_ids` | string[] | 否 | 默认 `[]` |
| `tool_ids` | string[] | 否 | 默认 `[]` |
| `temperature` | number | 否 | 默认 `0.7` |

**响应**：`200`

#### `GET /api/experts/{expert_id}`

专家详情

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `expert_id` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `PUT /api/experts/{expert_id}`

更新专家

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `expert_id` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string? | 否 | — |
| `description` | string? | 否 | — |
| `emoji` | string? | 否 | — |
| `persona` | string? | 否 | — |
| `skill_ids` | string[]? | 否 | — |
| `kb_ids` | string[]? | 否 | — |
| `tool_ids` | string[]? | 否 | — |
| `temperature` | number? | 否 | — |
| `enabled` | boolean? | 否 | — |

**响应**：`200`

#### `DELETE /api/experts/{expert_id}`

删除专家

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `expert_id` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

### 3.9 技能（`skills`）

#### `GET /api/skills`

技能列表

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `POST /api/skills`

新建技能

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `x-workspace` | header | string? | 否 | — |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 | — |
| `description` | string | 否 | — |
| `system_prompt` | string | 否 | — |
| `tools` | string[] | 否 | 默认 `[]` |

**响应**：`200`

#### `POST /api/skills/install`

安装技能

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `x-workspace` | header | string? | 否 | — |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `url` | string | 是 | — |
| `ref` | string | 否 | — |
| `path` | string | 否 | — |

**响应**：`200`

#### `POST /api/skills/install/preview`

安装预检：先看会写入哪些文件

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `x-workspace` | header | string? | 否 | — |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `url` | string | 是 | — |
| `ref` | string | 否 | — |
| `path` | string | 否 | — |

**响应**：`200`

#### `GET /api/skills/market`

技能市场

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `q` | query | string | 否 | — |
| `source` | query | string | 否 | 默认 `github` |
| `limit` | query | integer | 否 | 默认 `20`；最小 1；最大 50 |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `PUT /api/skills/{skill_id}`

更新技能

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `skill_id` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string? | 否 | — |
| `description` | string? | 否 | — |
| `system_prompt` | string? | 否 | — |
| `tools` | string[]? | 否 | — |
| `enabled` | boolean? | 否 | — |

**响应**：`200`

#### `DELETE /api/skills/{skill_id}`

删除技能

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `skill_id` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `GET /api/skills/{skill_id}/file`

读取技能内单个文件

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `skill_id` | path | string | 是 | — |
| `path` | query | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `GET /api/skills/{skill_id}/files`

技能文件清单

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `skill_id` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `POST /api/skills/{skill_id}/update`

从来源重新拉取技能

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `skill_id` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

### 3.10 MCP 服务（`mcp`）

#### `GET /api/mcp/servers`

MCP 服务列表

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `POST /api/mcp/servers`

接入 MCP 服务

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `x-workspace` | header | string? | 否 | — |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 | — |
| `transport` | string | 否 | 默认 `stdio` |
| `command` | string | 否 | — |
| `args` | string[] | 否 | — |
| `env` | map<string> | 否 | — |
| `url` | string | 否 | — |

**响应**：`200`

#### `PATCH /api/mcp/servers/{server_id}`

更新 MCP 服务

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `server_id` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string? | 否 | — |
| `enabled` | boolean? | 否 | — |

**响应**：`200`

#### `DELETE /api/mcp/servers/{server_id}`

移除 MCP 服务

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `server_id` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `POST /api/mcp/servers/{server_id}/call`

直连调用一次工具（管理端调试用）。

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `server_id` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `tool` | string | 是 | — |
| `arguments` | object | 否 | — |

**响应**：`200`

#### `POST /api/mcp/servers/{server_id}/refresh`

重新拉取工具清单

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `server_id` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

### 3.11 评测（`eval`）

#### `GET /api/eval/sets`

评测集列表

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `kb_id` | query | string? | 否 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `POST /api/eval/sets`

新建评测集

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `x-workspace` | header | string? | 否 | — |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `kb_id` | string | 是 | — |
| `name` | string | 是 | — |
| `items` | object[] | 是 | — |

<details><summary>嵌套字段</summary>

`items[]` 元素
| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `query` | string | 是 | — |
| `expected_doc` | string | 否 | — |
| `expected_contains` | string[] | 否 | — |
| `expected_text` | string | 否 | — |

</details>

**响应**：`200`

#### `DELETE /api/eval/sets/{set_id}`

删除评测集

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `set_id` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `POST /api/eval/sets/{set_id}/run`

mode 可选：vector / lexical / hybrid，用于对比不同检索策略。

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `set_id` | path | string | 是 | — |
| `mode` | query | string? | 否 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

### 3.12 jobs（`jobs`）

#### `GET /api/jobs`

后台任务列表

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `status` | query | string? | 否 | — |
| `limit` | query | integer | 否 | 默认 `50` |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `GET /api/jobs/stats`

队列统计（各状态计数）

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `GET /api/jobs/{job_id}`

任务详情（含 progress）

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `job_id` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `POST /api/jobs/{job_id}/retry`

重试失败任务

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `job_id` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

### 3.13 平台管理（`platform`）

#### `GET /api/apikeys`

API Key 列表（不返回明文）

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `POST /api/apikeys`

签发 API Key，明文仅此一次返回

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `x-workspace` | header | string? | 否 | — |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 | — |
| `workspace_id` | string? | 否 | — |
| `scopes` | string[] | 否 | — |

**响应**：`200`

#### `DELETE /api/apikeys/{key_id}`

撤销 API Key

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `key_id` | path | string | 是 | — |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `GET /api/audit`

审计日志

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `limit` | query | integer | 否 | 默认 `100` |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `GET /api/workspaces`

工作区列表按账号可见范围过滤：管理员看全部，成员只看归属。

**鉴权**：会话

**响应**：`200`

#### `POST /api/workspaces`

新建工作区

**鉴权**：会话 · 管理员

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 | — |
| `description` | string | 否 | — |

**响应**：`200`

#### `PATCH /api/workspaces/{ws_id}`

更新工作区

**鉴权**：会话 · 管理员

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `ws_id` | path | string | 是 | — |

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `name` | string | 是 | — |
| `description` | string? | 否 | — |

**响应**：`200`

#### `DELETE /api/workspaces/{ws_id}`

只允许删除空工作区，避免误删带走数据。

**鉴权**：会话 · 管理员

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `ws_id` | path | string | 是 | — |

**响应**：`200`

### 3.14 只读浏览（`browse`）

#### `GET /api/browse/asset`

单资源代理：CSS/图片/脚本经服务端取回，绕开防盗链与混合内容。

票据由全站中间件统一校验（/api/browse/ 在预览前缀里，仅放行 GET）。

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `url` | query | string | 是 | 最短 3 |
| `ticket` | query | string | 否 | — |

**响应**：`200`

#### `GET /api/browse/read`

抽取网页正文

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `url` | query | string | 是 | 最短 3 |

**响应**：`200`

#### `GET /api/browse/themes`

PPT 编辑器的主题色板。放在 /browse 下是为了避开 /projects/{project} 的路径吞并。

**鉴权**：会话

**响应**：`200`

#### `GET /api/browse/ticket`

给 iframe / img 用的短期票据（15 分钟，只放行预览类 GET）。

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `x-workspace` | header | string? | 否 | — |

**响应**：`200`

#### `GET /api/browse/web`

网页代理（只读）

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `url` | query | string | 是 | 最短 3 |
| `ticket` | query | string | 否 | — |

**响应**：`200`

### 3.15 可观测（`observability`）

#### `DELETE /api/obs/`

清空指标

**鉴权**：会话

**响应**：`200`

#### `GET /api/obs/llm`

模型调用统计

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `limit` | query | integer | 否 | 默认 `50`；最大 500 |

**响应**：`200`

#### `GET /api/obs/requests`

请求耗时分布

**鉴权**：会话

**参数**

| 名称 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | :---: | --- |
| `limit` | query | integer | 否 | 默认 `50`；最大 500 |

**响应**：`200`

#### `GET /api/obs/summary`

整体概览

**鉴权**：会话

**响应**：`200`

### 3.16 开放 API（/v1）（`openapi`）

#### `POST /v1/agent/run`

运行智能体

**鉴权**：Key · agent

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `task` | string | 是 | — |
| `tool_ids` | string[] | 否 | — |
| `kb_ids` | string[] | 否 | — |

**响应**：`200`

#### `POST /v1/chat/completions`

对话补全（OpenAI 兼容，支持 stream）

**鉴权**：Key · chat

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `model` | string? | 否 | — |
| `messages` | object[] | 是 | — |
| `stream` | boolean | 否 | 默认 `False` |
| `temperature` | number? | 否 | — |
| `kb_ids` | string[] | 否 | — |
| `expert_id` | string? | 否 | — |

<details><summary>嵌套字段</summary>

`messages[]` 元素
| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `role` | string | 是 | — |
| `content` | string | 是 | — |

</details>

**响应**：`200`

#### `POST /v1/search`

检索知识库

**鉴权**：Key · search

**请求体**（`application/json`）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `query` | string | 是 | — |
| `kb_ids` | string[] | 否 | — |
| `top_k` | integer | 否 | 默认 `4` |
| `mode` | string? | 否 | — |

**响应**：`200`
