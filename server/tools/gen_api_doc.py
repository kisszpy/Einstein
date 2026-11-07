"""从 FastAPI 应用导出 docs/API参考.md。

不依赖运行中的服务：直接 import 应用取 `app.openapi()`，因此可以在 CI 里跑，
也能在改完路由后立刻重新生成，避免文档与实现漂移。

用法：
    python tools/gen_api_doc.py                 # 写入 docs/API参考.md
    python tools/gen_api_doc.py --check         # 只比对，不写文件；有差异则退出码 1
    python tools/gen_api_doc.py --out x.md      # 写到指定路径
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

METHODS = ("get", "post", "put", "patch", "delete")
TAG_TITLES = {
    "health": "健康检查",
    "auth": "登录与用户",
    "profile": "个人资料",
    "chat": "对话（SSE）",
    "conversations": "会话历史",
    "agent": "智能体",
    "kb": "知识库",
    "projects": "项目与文件",
    "experts": "专家",
    "skills": "技能",
    "mcp": "MCP 服务",
    "eval": "评测",
    "platform": "平台管理",
    "browse": "只读浏览",
    "observability": "可观测",
    "openapi": "开放 API（/v1）",
}
TAG_ORDER = [
    "health", "auth", "profile", "chat", "conversations", "agent", "kb",
    "projects", "experts", "skills", "mcp", "eval", "jobs", "platform", "browse",
    "observability", "openapi",
]

# 路由函数没有中文 docstring 时用的中文名。有中文说明的接口不会被这张表覆盖，
# 因此后续给路由补上 docstring 即可逐步淘汰这里的条目。
SUMMARY_ZH = {
    ("GET", "/api/health"): "健康检查",
    ("POST", "/api/auth/login"): "登录，返回会话令牌",
    ("POST", "/api/auth/logout"): "退出登录，令牌立即失效",
    ("GET", "/api/auth/me"): "当前登录用户",
    ("GET", "/api/users"): "用户列表",
    ("POST", "/api/users"): "新建用户",
    ("PATCH", "/api/users/{user_id}"): "更新用户（昵称 / 角色 / 工作区 / 启停）",
    ("DELETE", "/api/users/{user_id}"): "删除用户",
    ("POST", "/api/users/{user_id}/password"): "重置密码",
    ("GET", "/api/conversations"): "会话列表",
    ("GET", "/api/conversations/{conversation_id}"): "会话详情（含全部消息）",
    ("DELETE", "/api/conversations/{conversation_id}"): "删除会话",
    ("GET", "/api/agent/runs"): "智能体运行记录",
    ("GET", "/api/agent/tools"): "可用工具清单",
    ("GET", "/api/kb"): "知识库列表",
    ("POST", "/api/kb"): "新建知识库",
    ("GET", "/api/kb/{kb_id}/documents"): "文档列表（含解析状态与切片数）",
    ("GET", "/api/kb/{kb_id}/documents/{doc_id}/versions"): "文档历史版本",
    ("GET", "/api/projects"): "项目列表",
    ("POST", "/api/projects"): "新建项目",
    ("DELETE", "/api/projects/{project}"): "删除项目",
    ("GET", "/api/projects/{project}/tree"): "项目目录树",
    ("GET", "/api/projects/{project}/file"): "读取单个文件",
    ("PUT", "/api/projects/{project}/file"): "写入单个文件",
    ("GET", "/api/projects/{project}/download"): "下载单个文件",
    ("GET", "/api/projects/{project}/archive"): "打包下载整个项目",
    ("GET", "/api/projects/{project}/deck"): "读取 PPT 结构化数据",
    ("PUT", "/api/projects/{project}/deck"): "保存 PPT 结构化数据",
    ("GET", "/api/experts"): "专家列表",
    ("POST", "/api/experts"): "新建专家",
    ("GET", "/api/experts/{expert_id}"): "专家详情",
    ("PUT", "/api/experts/{expert_id}"): "更新专家",
    ("DELETE", "/api/experts/{expert_id}"): "删除专家",
    ("GET", "/api/skills"): "技能列表",
    ("POST", "/api/skills"): "新建技能",
    ("PUT", "/api/skills/{skill_id}"): "更新技能",
    ("DELETE", "/api/skills/{skill_id}"): "删除技能",
    ("GET", "/api/skills/market"): "技能市场",
    ("POST", "/api/skills/install/preview"): "安装预检：先看会写入哪些文件",
    ("POST", "/api/skills/install"): "安装技能",
    ("POST", "/api/skills/{skill_id}/update"): "从来源重新拉取技能",
    ("GET", "/api/skills/{skill_id}/files"): "技能文件清单",
    ("GET", "/api/skills/{skill_id}/file"): "读取技能内单个文件",
    ("GET", "/api/mcp/servers"): "MCP 服务列表",
    ("POST", "/api/mcp/servers"): "接入 MCP 服务",
    ("PATCH", "/api/mcp/servers/{server_id}"): "更新 MCP 服务",
    ("DELETE", "/api/mcp/servers/{server_id}"): "移除 MCP 服务",
    ("POST", "/api/mcp/servers/{server_id}/refresh"): "重新拉取工具清单",
    ("GET", "/api/eval/sets"): "评测集列表",
    ("POST", "/api/eval/sets"): "新建评测集",
    ("DELETE", "/api/eval/sets/{set_id}"): "删除评测集",
    ("GET", "/api/jobs"): "后台任务列表",
    ("GET", "/api/jobs/stats"): "队列统计（各状态计数）",
    ("GET", "/api/jobs/{job_id}"): "任务详情（含 progress）",
    ("POST", "/api/jobs/{job_id}/retry"): "重试失败任务",
    ("GET", "/api/apikeys"): "API Key 列表（不返回明文）",
    ("POST", "/api/apikeys"): "签发 API Key，明文仅此一次返回",
    ("DELETE", "/api/apikeys/{key_id}"): "撤销 API Key",
    ("GET", "/api/audit"): "审计日志",
    ("POST", "/api/workspaces"): "新建工作区",
    ("PATCH", "/api/workspaces/{ws_id}"): "更新工作区",
    ("GET", "/api/browse/web"): "网页代理（只读）",
    ("GET", "/api/browse/read"): "抽取网页正文",
    ("DELETE", "/api/obs/"): "清空指标",
    ("GET", "/api/obs/summary"): "整体概览",
    ("GET", "/api/obs/requests"): "请求耗时分布",
    ("GET", "/api/obs/llm"): "模型调用统计",
    ("POST", "/v1/chat/completions"): "对话补全（OpenAI 兼容，支持 stream）",
    ("POST", "/v1/search"): "检索知识库",
    ("POST", "/v1/agent/run"): "运行智能体",
}

# 需要管理员权限的接口（依据 routes/auth.py、routes/platform.py 里的 current_admin）
ADMIN_ONLY = {
    ("GET", "/api/users"),
    ("POST", "/api/users"),
    ("PATCH", "/api/users/{user_id}"),
    ("POST", "/api/users/{user_id}/password"),
    ("DELETE", "/api/users/{user_id}"),
    ("POST", "/api/workspaces"),
    ("PATCH", "/api/workspaces/{ws_id}"),
    ("DELETE", "/api/workspaces/{ws_id}"),
}
PUBLIC = {("GET", "/api/health"), ("POST", "/api/auth/login")}
# 开放 API 的 scope（依据 routes/openapi.py 的 require_scope 调用）
SCOPES = {
    ("POST", "/v1/chat/completions"): "chat",
    ("POST", "/v1/search"): "search",
    ("POST", "/v1/agent/run"): "agent",
}

HEADER = """# Einstein API 参考

> 本文由 `server/tools/gen_api_doc.py` 从应用自身的 OpenAPI schema 生成，**请勿手工编辑**。
> 改完路由后运行 `python tools/gen_api_doc.py` 重新生成；CI 用 `--check` 校验是否漂移。
>
> 接口数：@OPS@ 个操作 / @PATHS@ 个路径 · 应用版本 @VERSION@

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
TOKEN=$(curl -s --noproxy '*' -X POST http://127.0.0.1:8000/api/auth/login \\
  -H 'Content-Type: application/json' \\
  -d '{"username":"admin","password":"***"}' | python -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -s --noproxy '*' http://127.0.0.1:8000/api/kb -H "Authorization: Bearer $TOKEN"

# 开放 API
curl -s --noproxy '*' http://127.0.0.1:8000/v1/search \\
  -H "Authorization: Bearer sk-xxxx" -H 'Content-Type: application/json' \\
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

响应为 `text/event-stream`，逐帧 `event: <name>\\ndata: <json>\\n\\n`。事件顺序：

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
"""


def _resolve(spec: dict, node: dict, depth: int = 0) -> dict:
    while isinstance(node, dict) and "$ref" in node and depth < 8:
        name = node["$ref"].rsplit("/", 1)[-1]
        node = (spec.get("components", {}).get("schemas") or {}).get(name, {})
        depth += 1
    return node if isinstance(node, dict) else {}


def _type(spec: dict, node: dict) -> str:
    node = _resolve(spec, node)
    if not node:
        return "any"
    if "anyOf" in node:
        parts, nullable = [], False
        for sub in node["anyOf"]:
            if _resolve(spec, sub).get("type") == "null":
                nullable = True
            else:
                parts.append(_type(spec, sub))
        text = " | ".join(dict.fromkeys(p for p in parts if p)) or "any"
        return text + ("?" if nullable else "")
    kind = node.get("type", "object")
    if kind == "array":
        return f"{_type(spec, node.get('items', {}))}[]"
    if kind == "object" and isinstance(node.get("additionalProperties"), dict):
        return f"map<{_type(spec, node['additionalProperties'])}>"
    if kind == "string" and node.get("enum"):
        return "enum"
    fmt = node.get("format")
    return f"{kind}({fmt})" if fmt else kind


def _note(spec: dict, node: dict) -> str:
    node = _resolve(spec, node)
    bits = []
    desc = (node.get("description") or "").strip().replace("\n", " ")
    if desc:
        bits.append(desc)
    if node.get("enum"):
        bits.append("取值：" + " / ".join(f"`{v}`" for v in node["enum"]))
    if "default" in node and node["default"] not in (None, ""):
        bits.append(f"默认 `{node['default']}`")
    for key, label in (("minimum", "最小"), ("maximum", "最大"), ("maxLength", "最长"), ("minLength", "最短")):
        if key in node:
            bits.append(f"{label} {node[key]}")
    return "；".join(bits).replace("|", "/")


def _shape(spec: dict, schema: dict, depth: int = 0) -> list[str]:
    """把请求体/响应体摊平成字段表；对象/对象数组再展开一层。"""
    schema = _resolve(spec, schema)
    props = schema.get("properties") or {}
    if not props:
        return []
    required = set(schema.get("required") or [])
    rows = ["| 字段 | 类型 | 必填 | 说明 |", "| --- | --- | :---: | --- |"]
    nested = []
    for name, raw in props.items():
        rows.append(
            f"| `{name}` | {_type(spec, raw)} | {'是' if name in required else '否'} | {_note(spec, raw) or '—'} |"
        )
        if depth >= 1:
            continue
        node = _resolve(spec, raw)
        if node.get("type") == "array":
            node, label = _resolve(spec, node.get("items") or {}), f"`{name}[]` 元素"
        else:
            label = f"`{name}` 内层"
        if node.get("properties"):
            sub = _shape(spec, node, depth + 1)
            if sub:
                nested.append(label)
                nested.extend(sub)
                nested.append("")
    if nested:
        rows.append("")
        rows.append("<details><summary>嵌套字段</summary>\n")
        rows.extend(nested)
        rows.append("</details>")
    return rows


def _auth_label(method: str, path: str) -> str:
    if (method, path) in PUBLIC:
        return "公开"
    if path.startswith("/v1/"):
        scope = SCOPES.get((method, path))
        return f"Key · {scope}" if scope else "Key"
    if (method, path) in ADMIN_ONLY:
        return "会话 · 管理员"
    return "会话"


def _chinese(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _title(op: dict, method: str, path: str) -> str:
    """优先用路由的中文说明首行，其次用补充中文名，最后退回英文 summary。"""
    desc = (op.get("description") or "").strip()
    if desc and _chinese(desc):
        line = desc.splitlines()[0].strip()
        if line and not line.startswith("#"):
            return line
    return SUMMARY_ZH.get((method, path)) or op.get("summary") or "—"


def _short(op: dict, method: str, path: str) -> str:
    line = _title(op, method, path)
    return line if len(line) <= 46 else line[:45] + "…"


def build(spec: dict) -> str:
    paths = spec.get("paths") or {}
    groups: "OrderedDict[str, list]" = OrderedDict()
    operations = 0
    for path, ops in sorted(paths.items()):
        for method in METHODS:
            op = ops.get(method)
            if not isinstance(op, dict):
                continue
            operations += 1
            tag = (op.get("tags") or ["misc"])[0]
            groups.setdefault(tag, []).append((path, method, op))

    ordered = [t for t in TAG_ORDER if t in groups] + [t for t in groups if t not in TAG_ORDER]
    out = [
        HEADER.replace("@OPS@", str(operations))
        .replace("@PATHS@", str(len(paths)))
        .replace("@VERSION@", str((spec.get("info") or {}).get("version", "—")))
    ]
    for tag in ordered:
        for path, method, op in groups[tag]:
            out.append(f"| `{method.upper()}` | `{path}` | {_short(op, method.upper(), path)} | {_auth_label(method.upper(), path)} |")

    out.append("\n## 3. 接口详情\n")
    for tag in ordered:
        out.append(f"### 3.{ordered.index(tag) + 1} {TAG_TITLES.get(tag, tag)}（`{tag}`）\n")
        for path, method, op in groups[tag]:
            out.append(f"#### `{method.upper()} {path}`\n")
            desc = (op.get("description") or "").strip()
            if desc and _chinese(desc):
                out.append(desc + "\n")
            else:
                out.append(_title(op, method.upper(), path) + "\n")
            out.append(f"**鉴权**：{_auth_label(method.upper(), path)}\n")
            if path == "/api/chat":
                out.append("事件协议与保活约定见 [1.6 SSE 流式协议](#16-sse-流式协议post-apichat)。\n")

            params = [
                p
                for p in (op.get("parameters") or [])
                if isinstance(p, dict) and not (p.get("in") == "header" and p.get("name") == "authorization")
            ]
            if params:
                out.append("**参数**\n")
                out.append("| 名称 | 位置 | 类型 | 必填 | 说明 |")
                out.append("| --- | --- | --- | :---: | --- |")
                for p in params:
                    out.append(
                        f"| `{p.get('name')}` | {p.get('in')} | {_type(spec, p.get('schema') or {})} "
                        f"| {'是' if p.get('required') else '否'} | {_note(spec, p.get('schema') or {}) or '—'} |"
                    )
                out.append("")

            body = op.get("requestBody") or {}
            if body:
                content = body.get("content") or {}
                schema = ((content.get("application/json") or {}).get("schema")) or {}
                rows = _shape(spec, schema)
                if rows:
                    out.append("**请求体**（`application/json`）\n")
                    out.extend(rows)
                    out.append("")

            responses = op.get("responses") or {}
            codes = [c for c in responses if str(c).startswith("2")]
            if codes:
                out.append(f"**响应**：`{'` / `'.join(str(c) for c in codes)}`\n")
                ok = responses[codes[0]]
                content = (ok.get("content") or {}).get("application/json") or {}
                rows = _shape(spec, (content.get("schema") or {}))
                if rows:
                    out.extend(rows)
                    out.append("")
    return "\n".join(out).replace("\n\n\n", "\n\n").rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target = args.out or os.path.join(os.path.dirname(root), "docs", "API参考.md")

    from app.main import create_app

    text = build(create_app().openapi())

    if args.check:
        try:
            with open(target, encoding="utf-8") as fh:
                old = fh.read()
        except FileNotFoundError:
            print(f"[api-doc] 缺少 {target}")
            return 1
        if old != text:
            print(f"[api-doc] {target} 与代码不一致，请运行 python tools/gen_api_doc.py")
            return 1
        print(f"[api-doc] {target} 已是最新")
        return 0

    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    size = len(text.encode("utf-8"))
    print(f"[api-doc] 已写入 {target}（{size / 1024:.1f} KB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
