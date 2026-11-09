"""MCP 服务器注册表与客户端。

配置持久化在 data/mcp_servers.json（与知识库 JSON 适配器同一替换点：
生产换 PostgreSQL 时接口不变）。连接按需建立——刷新工具清单、执行调用
时开一个会话，用完即断；开发期最省心，生产可替换为常驻连接池。

工具 ID 约定：`mcp:{server_id}:{tool_name}`，Tool Registry 按 ID 前缀
路由，上层（智能体/对话）对内置工具与 MCP 工具无感知差异。
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.services import audit
from app.services.state import blob
from typing import Literal

import mcp.client.stdio as _stdio
import mcp.client.streamable_http as _http
from mcp import ClientSession, StdioServerParameters
from pydantic import BaseModel, Field

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_STORE_PATH = _DATA_DIR / "mcp_servers.json"
_STATE = blob("mcp", _STORE_PATH)

TOOL_CALL_TIMEOUT = 120  # 单次工具调用上限（秒）
LIST_TIMEOUT = 20        # 刷新工具清单上限（秒）
MAX_CONCURRENT_PER_SERVER = 4  # 单服务器并发调用上限（执行隔离）


class McpToolDef(BaseModel):
    name: str
    description: str = ""
    schema_: dict = Field(default_factory=dict, alias="schema")
    model_config = {"populate_by_name": True}


class McpServer(BaseModel):
    id: str
    name: str
    transport: Literal["stdio", "http"] = "stdio"
    # stdio
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)  # 落盘为加密值（enc:v1:…）
    # http (streamable)
    url: str = ""

    enabled: bool = True
    # 工具审批：空 = 全部放行（兼容存量配置）；非空 = 仅列表内的工具可调用
    approved_tools: list[str] = Field(default_factory=list)
    # 运行时缓存（刷新后写入）
    status: Literal["unknown", "ok", "error"] = "unknown"
    tools: list[McpToolDef] = Field(default_factory=list)
    last_error: str = ""
    refreshed_at: str = ""
    workspace_id: str = "default"
    # 归属账号：'' = 平台预置连接（全员可见）；其余为添加者 id（仅本人可见）
    owner_id: str = ""

    def connection_summary(self) -> str:
        if self.transport == "stdio":
            return " ".join([self.command, *self.args]).strip()
        return self.url


# ---------- 凭证加密（零第三方依赖：SHA-256 密钥流 + HMAC 认证） ----------
#
# v1 只有"加密"没有"认证"，密文被改一位也能解出一段乱码而不报错；
# v2 补上 Encrypt-then-MAC：iv+ct 用独立密钥做 HMAC-SHA256 截断 16 字节。
# 没有引 cryptography 是因为目标环境是离线 pip 源，装不上 Fernet；
# 生产若要换标准 AEAD，只需在 decrypt_value 里加一条 enc:v3 分支并重加密存量。

_ENC_PREFIX_V1 = "enc:v1:"
_ENC_PREFIX = "enc:v2:"
_MAC_LEN = 16


def _keystream_xor(key: bytes, iv: bytes, data: bytes) -> bytes:
    out = bytearray()
    counter = 0
    for i in range(len(data)):
        if i % 32 == 0:
            block = hashlib.sha256(key + iv + counter.to_bytes(4, "big")).digest()
            counter += 1
        out.append(data[i] ^ block[i % 32])
    return bytes(out)


def _env_keys() -> tuple[bytes, bytes]:
    secret = get_settings().secret_key.encode("utf-8")
    enc_key = hashlib.sha256(b"mcp-env:" + secret).digest()
    mac_key = hashlib.sha256(b"mcp-mac:" + secret).digest()
    return enc_key, mac_key


def encrypt_value(value: str) -> str:
    """加密单个凭证值。明文原样返回（向后兼容未加密的存量配置）。"""
    if not value or value.startswith((_ENC_PREFIX, _ENC_PREFIX_V1)):
        return value
    enc_key, mac_key = _env_keys()
    iv = os.urandom(16)
    ct = _keystream_xor(enc_key, iv, value.encode("utf-8"))
    mac = hmac.new(mac_key, iv + ct, hashlib.sha256).digest()[:_MAC_LEN]
    return _ENC_PREFIX + base64.urlsafe_b64encode(iv + ct + mac).decode()


def decrypt_value(value: str) -> str:
    if value.startswith(_ENC_PREFIX_V1):
        # 存量 v1 密文：无 MAC，只能按老逻辑解；下次写入会自动升级到 v2。
        try:
            key = hashlib.sha256(("mcp-env:" + get_settings().secret_key).encode()).digest()
            raw = base64.urlsafe_b64decode(value[len(_ENC_PREFIX_V1):])
            iv, ct = raw[:16], raw[16:]
            return _keystream_xor(key, iv, ct).decode("utf-8")
        except Exception:  # noqa: BLE001 — 密钥不匹配等，返回占位避免崩溃
            return ""
    if not value.startswith(_ENC_PREFIX):
        return value
    try:
        enc_key, mac_key = _env_keys()
        raw = base64.urlsafe_b64decode(value[len(_ENC_PREFIX):])
        iv, ct, mac = raw[:16], raw[16:-_MAC_LEN], raw[-_MAC_LEN:]
        expect = hmac.new(mac_key, iv + ct, hashlib.sha256).digest()[:_MAC_LEN]
        if not hmac.compare_digest(mac, expect):
            # 密钥换了或被篡改：拒绝解密，而不是把乱码当凭证去连 MCP。
            print("[mcp] 凭证完整性校验失败（密钥变更或密文被改）", flush=True)
            return ""
        return _keystream_xor(enc_key, iv, ct).decode("utf-8")
    except Exception:  # noqa: BLE001
        return ""


def _encrypt_env(env: dict[str, str]) -> dict[str, str]:
    return {k: encrypt_value(v) for k, v in env.items()}


def _decrypt_env(env: dict[str, str]) -> dict[str, str]:
    return {k: decrypt_value(v) for k, v in env.items()}


# ---------- 持久化 ----------

_servers: dict[str, McpServer] | None = None
_lock = threading.Lock()


def _load() -> dict[str, McpServer]:
    try:
        payload = _STATE.read() or {}
        return {sid: McpServer(**v) for sid, v in payload.items()}
    except Exception:  # noqa: BLE001 — 损坏则重建
        return {}


def _save(servers: dict[str, McpServer]) -> None:
    payload = {sid: s.model_dump(by_alias=True) for sid, s in servers.items()}
    _STATE.write(payload)


def _registry() -> dict[str, McpServer]:
    global _servers
    with _lock:
        if _servers is None:
            _servers = _load()
        return _servers


# ---------- CRUD ----------

def list_servers(
    workspace_id: str | None = None, owner_id: str | None = None,
) -> list[McpServer]:
    rows = list(_registry().values())
    if workspace_id:
        rows = [s for s in rows if s.workspace_id == workspace_id]
    if owner_id is not None:
        rows = [s for s in rows if s.owner_id in ("", owner_id)]
    return rows


def get_server(server_id: str) -> McpServer | None:
    return _registry().get(server_id)


def visible_server(server_id: str, workspace_id: str, owner_id: str) -> McpServer | None:
    """按 id 取连接并校验可见性；不可见一律当不存在（防越权探测）。"""
    server = _registry().get(server_id)
    if server is None or server.workspace_id != workspace_id:
        return None
    return server if server.owner_id in ("", owner_id) else None


def can_edit(server: McpServer, owner_id: str, is_admin: bool = False) -> bool:
    """创建者能改自己的；预置连接（'' ）只有管理员能改。"""
    if server.owner_id == owner_id:
        return True
    return server.owner_id == "" and is_admin


def add_server(
    name: str,
    transport: Literal["stdio", "http"],
    command: str = "",
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    url: str = "",
    workspace_id: str = "default",
    owner_id: str = "",
) -> McpServer:
    server = McpServer(
        id=uuid.uuid4().hex[:12],
        name=name,
        transport=transport,
        command=command,
        args=args or [],
        env=_encrypt_env(env or {}),  # 凭证加密落盘
        url=url,
        workspace_id=workspace_id,
        owner_id=owner_id,
    )
    registry = _registry()
    with _lock:
        registry[server.id] = server
        _save(registry)
    return server


def update_server(server_id: str, patch: dict) -> McpServer | None:
    registry = _registry()
    server = registry.get(server_id)
    if server is None:
        return None
    allowed = {"name", "enabled", "command", "args", "env", "url", "transport"}
    updates = {k: v for k, v in patch.items() if k in allowed}
    if "env" in updates and isinstance(updates["env"], dict):
        updates["env"] = _encrypt_env(updates["env"])
    updated = server.model_copy(update=updates)
    # 配置变更后工具缓存失效
    if {"command", "args", "env", "url", "transport"} & updates.keys():
        updated = updated.model_copy(
            update={"tools": [], "status": "unknown", "last_error": "", "refreshed_at": ""}
        )
    with _lock:
        registry[server_id] = updated
        _save(registry)
    return updated


def remove_server(server_id: str) -> bool:
    registry = _registry()
    if server_id not in registry:
        return False
    with _lock:
        del registry[server_id]
        _save(registry)
    return True


# ---------- 连接与调用 ----------

@asynccontextmanager
async def _connect(server: McpServer):
    """按传输方式建立会话。用 asyncio.wait_for 兜底防止握手挂死。"""
    if server.transport == "stdio":
        # 合并环境变量：stdio 子进程通常需要继承 PATH 才能解析命令；凭证先解密
        env = {**_stdio.get_default_environment(), **_decrypt_env(server.env)}
        params = StdioServerParameters(command=server.command, args=server.args, env=env)
        async with _stdio.stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), LIST_TIMEOUT)
                yield session
    else:
        async with _http.streamable_http_client(server.url) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), LIST_TIMEOUT)
                yield session


def _fmt_exc(exc: BaseException) -> str:
    """展开 ExceptionGroup，把子异常也带出来（TaskGroup 常包一层）。"""
    parts = []
    stack: list[BaseException] = [exc]
    while stack:
        e = stack.pop()
        if isinstance(e, BaseExceptionGroup):
            stack.extend(e.exceptions)
        else:
            parts.append(f"{type(e).__name__}: {e}")
    return " | ".join(parts) or f"{type(exc).__name__}: {exc}"


async def refresh_server(server_id: str) -> McpServer | None:
    """连接服务器拉取工具清单并缓存。"""
    registry = _registry()
    server = registry.get(server_id)
    if server is None:
        return None
    try:
        async with _connect(server) as session:
            result = await asyncio.wait_for(session.list_tools(), LIST_TIMEOUT)
            tools = [
                McpToolDef(
                    name=t.name,
                    description=t.description or "",
                    schema=(t.input_schema or {}) if isinstance(t.input_schema, dict) else {},
                )
                for t in result.tools
            ]
        updated = server.model_copy(
            update={
                "tools": tools,
                "status": "ok",
                "last_error": "",
                "refreshed_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    except Exception as exc:  # noqa: BLE001 — 连接失败记入状态，不抛
        updated = server.model_copy(
            update={
                "tools": [],
                "status": "error",
                "last_error": _fmt_exc(exc),
                "refreshed_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    with _lock:
        registry[server_id] = updated
        _save(registry)
    return updated


# 单服务器并发闸门（执行隔离）：一个 MCP server 卡死不能拖垮整站
_SEMS: dict[str, asyncio.Semaphore] = {}
_SEM_LOCK = threading.Lock()


def _semaphore(server_id: str) -> asyncio.Semaphore:
    with _SEM_LOCK:
        if server_id not in _SEMS:
            _SEMS[server_id] = asyncio.Semaphore(MAX_CONCURRENT_PER_SERVER)
        return _SEMS[server_id]


async def call_tool(server_id: str, tool_name: str, arguments: dict) -> str:
    """执行一次 MCP 工具调用，返回文本结果。含审批检查、并发隔离与审计。"""
    server = _registry().get(server_id)
    if server is None:
        raise ValueError(f"MCP 服务器不存在: {server_id}")
    if not server.enabled:
        raise ValueError(f"MCP 服务器已停用: {server.name}")
    t0 = datetime.now(timezone.utc)
    status = 200
    detail = ""
    try:
        if server.approved_tools and tool_name not in server.approved_tools:
            # 审批拒绝也要留审计，便于追查异常调用
            status = 403
            detail = "tool not approved"
            raise ValueError(f"工具 {tool_name} 未通过审批，请先在 MCP 管理页批准")
        async with _semaphore(server_id):
            async with _connect(server) as session:
                result = await asyncio.wait_for(
                    session.call_tool(tool_name, arguments=arguments), TOOL_CALL_TIMEOUT
                )
        parts: list[str] = []
        for item in result.content:
            text = getattr(item, "text", None)
            if text:
                parts.append(text)
        text = "\n".join(parts) if parts else json.dumps(
            {"isError": bool(result.is_error)}, ensure_ascii=False
        )
        if result.is_error:
            text = f"[MCP 工具报错] {text}"
            status = 502
        detail = f"{len(text)} chars"
        return text
    except ValueError:
        status = 403
        raise
    except asyncio.TimeoutError as exc:
        status = 504
        detail = "timeout"
        raise ValueError(f"MCP 工具调用超时（>{TOOL_CALL_TIMEOUT}s）") from exc
    except Exception as exc:  # noqa: BLE001
        status = 502
        detail = f"{type(exc).__name__}: {exc}"[:200]
        raise
    finally:
        elapsed_ms = (datetime.now(timezone.utc) - t0).total_seconds() * 1000
        audit.record(
            workspace_id=server.workspace_id,
            key_id=None,
            key_name=None,
            method="MCP_CALL",
            path=f"/mcp/{server_id}/{tool_name}",
            status=status,
            duration_ms=round(elapsed_ms, 1),
            detail=detail,
        )


def set_tool_approval(server_id: str, tool: str, approved: bool) -> McpServer | None:
    """把工具加入/移出审批白名单。首次收紧时从空（全放行）切到白名单模式。"""
    registry = _registry()
    server = registry.get(server_id)
    if server is None or not tool:
        return None
    current = list(server.approved_tools)
    if approved and tool not in current:
        current.append(tool)
    elif not approved and tool in current:
        current.remove(tool)
    updated = server.model_copy(update={"approved_tools": current})
    with _lock:
        registry[server_id] = updated
        _save(registry)
    return updated


def server_payload(server: McpServer) -> dict:
    d = server.model_dump(by_alias=True)
    # 凭证不回传明文：有配置就给占位符
    d["env"] = {k: ("•••" if v else "") for k, v in server.env.items()}
    d["has_env"] = any(v for v in server.env.values())
    d["connection"] = server.connection_summary()
    return d
