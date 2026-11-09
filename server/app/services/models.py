"""模型注册表：把「用哪个模型」从 .env 变成可管理的资源。

原先模型信息只有三行配置（LLM_BASE_URL / LLM_MODEL / LLM_API_KEY），换模型
要改文件重启，页面上看不到当前用的是什么，也没法让不同专家跑不同模型。
这里一张 `models` 表解决三件事：

1. **可见**：管理页能列出所有可用端点，显示连通性检测的结果与耗时；
2. **可选**：专家可指定自己的模型（写作要长上下文、代码要强推理）；
3. **可切**：`is_default` 一行生效，全站默认走它。

两条硬规则：

- **不预置任何行**。没有 `is_default=1` 时 `resolve()` 回落到 `.env`，
  所以老部署升级后行为完全不变；用户显式建模型并设为默认才切过来。
- **密钥与 MCP 凭证同一套加密**（`enc:v2:` + HMAC 认证）。`api_key` 列存密文，
  对外 payload 只给 `has_key` 与末四位，任何接口都不回显明文。

`resolve()` 在热路径上（每条消息、每次智能体运行都要调），所以带 3 秒进程内
缓存；任何写操作立刻清缓存，多实例下最坏是 3 秒的可见性延迟——模型配置不是
秒级强一致的场景，换这点延迟换来的是每条消息少两次查询。
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from app.core.config import get_settings
from app.db.engine import get_db
from app.db.rows import TableState
from app.db.schema import migrate
from app.db.tables import MODELS
from app.services.mcp import decrypt_value, encrypt_value
from app.services.state import JsonState

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_JSON_STORE = JsonState(_DATA_DIR / "models.json")
_lock = threading.Lock()

KINDS = ("chat", "embed")
_CACHE_TTL = 3.0
_cache: dict[str, tuple[float, "Resolved"]] = {}
_cache_guard = threading.Lock()


class Resolved(NamedTuple):
    """一次调用真正要用的端点三元组 + 采样参数。"""

    model_id: str          # 注册表 id；'' 表示回落到 .env
    base_url: str
    api_key: str
    model: str
    temperature: float | None
    max_output: int
    source: str            # "registry" | "env" —— 便于链路上标注来源
    price_in: float = 0.0
    price_out: float = 0.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _use_table() -> bool:
    return get_settings().store_backend != "json"


def _table() -> TableState:
    db = get_db()
    migrate(db)
    return TableState(MODELS, db)


def _load_all() -> dict[str, dict]:
    if _use_table():
        return {row["id"]: row for row in _table().rows()}
    payload = _JSON_STORE.read() or {}
    return {key: dict(value) for key, value in payload.items() if isinstance(value, dict)}


def _save_all(models: dict[str, dict]) -> None:
    if _use_table():
        _table().write(models)
    else:
        _JSON_STORE.write(models)
    _invalidate()


def _invalidate() -> None:
    with _cache_guard:
        _cache.clear()


# ---------------------------------------------------------------- 可见性


def visible(model: dict, workspace_id: str, owner_id: str) -> bool:
    """可见：平台预置（owner_id=''）与自己在当前空间建的模型。

    与专家/技能同一套语义：空间是硬边界，预置对全员开放，其余只看本人。
    """
    if model.get("workspace_id", "default") != workspace_id:
        return False
    return model.get("owner_id", "") in ("", owner_id)


def can_edit(model: dict, owner_id: str, is_admin: bool = False) -> bool:
    """可改：自己建的；预置模型只有管理员能改（改了影响全空间）。"""
    owner = model.get("owner_id", "")
    if owner == owner_id and owner != "":
        return True
    return owner == "" and is_admin


def list_models(
    workspace_id: str, owner_id: str, *, kind: str = "", include_disabled: bool = True
) -> list[dict]:
    rows = [
        m
        for m in _load_all().values()
        if visible(m, workspace_id, owner_id)
        and (not kind or m.get("kind", "chat") == kind)
        and (include_disabled or m.get("enabled", True))
    ]
    # 默认模型排最前，其余按创建时间（表本身按 created_at 排，这里只把默认提上来）
    rows.sort(key=lambda m: (not m.get("is_default", False), m.get("created_at", "")))
    return rows


def all_models() -> list[dict]:
    """全部模型行，不做可见性过滤。只给内部计费/统计用，禁止直接用于接口返回。"""
    return list(_load_all().values())


def get_model(model_id: str) -> dict | None:
    if not model_id:
        return None
    return _load_all().get(model_id)


def visible_model(model_id: str, workspace_id: str, owner_id: str) -> dict | None:
    model = get_model(model_id)
    if model is None or not visible(model, workspace_id, owner_id):
        return None
    return model


def default_model(workspace_id: str = "", owner_id: str = "") -> dict | None:
    """当前生效的默认模型行；没有则返回 None（调用方回落到 .env）。"""
    candidates = [m for m in _load_all().values() if m.get("is_default") and m.get("enabled", True)]
    if workspace_id:
        # 优先本空间的默认模型；没有就退到平台级（workspace_id='default' 且 owner 为空）
        local = [m for m in candidates if m.get("workspace_id", "default") == workspace_id]
        if local:
            candidates = local
    if not candidates:
        return None
    candidates.sort(key=lambda m: m.get("updated_at", ""), reverse=True)
    return candidates[0]


# ---------------------------------------------------------------- 增删改


def _normalize_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


def create_model(
    *,
    name: str,
    base_url: str,
    model: str,
    api_key: str = "",
    workspace_id: str = "default",
    owner_id: str = "",
    context_window: int = 0,
    max_output: int = 0,
    temperature: float = 0.7,
    price_in: float = 0.0,
    price_out: float = 0.0,
    kind: str = "chat",
    is_default: bool = False,
    tags: list[str] | None = None,
    note: str = "",
) -> dict:
    name = (name or "").strip()
    base_url = _normalize_url(base_url)
    model = (model or "").strip()
    if not name:
        raise ValueError("模型名称不能为空")
    if not base_url.startswith(("http://", "https://")):
        raise ValueError("base_url 必须以 http:// 或 https:// 开头")
    if not model:
        raise ValueError("model 不能为空")
    if kind not in KINDS:
        raise ValueError(f"kind 只支持 {'/'.join(KINDS)}")

    with _lock:
        store = _load_all()
        if any(m.get("name") == name and m.get("workspace_id") == workspace_id for m in store.values()):
            raise ValueError(f"当前空间已有名为「{name}」的模型")
        entry = {
            "id": uuid.uuid4().hex[:12],
            "workspace_id": workspace_id,
            "owner_id": owner_id,
            "name": name,
            "base_url": base_url,
            "model": model,
            "api_key": encrypt_value(api_key) if api_key else "",
            "context_window": int(context_window or 0),
            "max_output": int(max_output or 0),
            "temperature": float(temperature),
            "price_in": float(price_in or 0),
            "price_out": float(price_out or 0),
            "kind": kind,
            "is_default": bool(is_default),
            "enabled": True,
            "tags": list(tags or []),
            "note": note,
            "last_status": "unknown",
            "last_error": "",
            "last_checked_at": "",
            "created_at": _now(),
            "updated_at": _now(),
        }
        if entry["is_default"]:
            _demote_defaults(store, workspace_id)
        store[entry["id"]] = entry
        _save_all(store)
    return entry


def _demote_defaults(store: dict[str, dict], workspace_id: str) -> None:
    """同一空间至多一个默认模型 —— 两个默认等于没有默认。"""
    for other in store.values():
        if other.get("workspace_id") == workspace_id and other.get("is_default"):
            other["is_default"] = False
            other["updated_at"] = _now()


_UPDATABLE = {
    "name", "base_url", "model", "context_window", "max_output",
    "temperature", "price_in", "price_out", "kind", "is_default",
    "enabled", "tags", "note",
}


def update_model(model_id: str, patch: dict) -> dict | None:
    """局部更新。`api_key` 单独处理：空字符串表示「不改」，而不是「清空」。

    为什么不是「空即清空」：管理页保存其它字段时不该因为密钥框留空就丢掉密钥；
    真要清空请显式传 `clear_key=True`。
    """
    with _lock:
        store = _load_all()
        entry = store.get(model_id)
        if entry is None:
            return None
        for key, value in patch.items():
            if key not in _UPDATABLE or value is None:
                continue
            entry[key] = value
        if patch.get("base_url"):
            entry["base_url"] = _normalize_url(patch["base_url"])
        if "kind" in patch and patch["kind"] not in KINDS:
            raise ValueError(f"kind 只支持 {'/'.join(KINDS)}")
        if patch.get("api_key"):
            entry["api_key"] = encrypt_value(patch["api_key"])
            entry["last_status"] = "unknown"   # 换了密钥，旧结论作废
            entry["last_error"] = ""
        if patch.get("clear_key"):
            entry["api_key"] = ""
            entry["last_status"] = "unknown"
        if patch.get("is_default"):
            _demote_defaults(store, entry.get("workspace_id", "default"))
            entry["is_default"] = True
        entry["updated_at"] = _now()
        store[model_id] = entry
        _save_all(store)
    return entry


def delete_model(model_id: str) -> bool:
    """删模型。引用它的专家自动回到「跟随默认」——不静默降级到某个具体模型。"""
    with _lock:
        store = _load_all()
        if model_id not in store:
            return False
        del store[model_id]
        _save_all(store)
    _detach_experts(model_id)
    return True


def _detach_experts(model_id: str) -> None:
    """清掉专家的 model_id 引用（失败不影响删除本身）。"""
    try:
        from app.db.engine import get_db as _db
        from app.db.tables import EXPERTS

        _db().execute("UPDATE experts SET model_id = '' WHERE model_id = ?", (model_id,))
    except Exception:  # noqa: BLE001 — 表不存在（json 后端）时无需处理
        pass


def record_probe(model_id: str, *, ok: bool, error: str = "") -> dict | None:
    """记下连通性检测结果。用 upsert 单行写，不重写整域。"""
    with _lock:
        store = _load_all()
        entry = store.get(model_id)
        if entry is None:
            return None
        entry["last_status"] = "ok" if ok else "error"
        entry["last_error"] = "" if ok else error[:500]
        entry["last_checked_at"] = _now()
        store[model_id] = entry
        _save_all(store)
    return entry


# ---------------------------------------------------------------- 解析


def _from_env() -> Resolved:
    settings = get_settings()
    return Resolved(
        model_id="",
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        temperature=None,
        max_output=0,
        source="env",
    )


def resolve(model_id: str = "", *, workspace_id: str = "", owner_id: str = "") -> Resolved:
    """把模型 id 解析成可调用的端点。

    解析顺序：指定 id → 本空间默认模型 → `.env`。指定的 id 不存在或已停用时
    也回落，而不是抛错——一次配置被删不该让整个对话接口 500，链路里能看出
    `source="env"` 就够了。
    """
    key = model_id or ""
    now = time.monotonic()
    with _cache_guard:
        hit = _cache.get(key)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]

    entry = get_model(model_id) if model_id else default_model(workspace_id, owner_id)
    if entry is not None and not entry.get("enabled", True):
        entry = None
    if entry is None:
        resolved = _from_env()
    else:
        settings = get_settings()
        key_plain = decrypt_value(entry.get("api_key", "")) if entry.get("api_key") else ""
        resolved = Resolved(
            model_id=entry["id"],
            base_url=entry.get("base_url") or settings.llm_base_url,
            api_key=key_plain or settings.llm_api_key,
            model=entry.get("model") or settings.llm_model,
            temperature=entry.get("temperature"),
            max_output=int(entry.get("max_output") or 0),
            source="registry",
            price_in=float(entry.get("price_in") or 0),
            price_out=float(entry.get("price_out") or 0),
        )

    with _cache_guard:
        _cache[key] = (now, resolved)
        if len(_cache) > 64:               # 兜底：极端情况下不让缓存无限长
            for stale in [k for k, (ts, _) in _cache.items() if now - ts > _CACHE_TTL]:
                _cache.pop(stale, None)
    return resolved


# ---------------------------------------------------------------- 对外视图


def model_payload(model: dict) -> dict:
    """接口返回体：密钥只给「有没有」和末四位，永不回明文。"""
    encrypted = model.get("api_key", "")
    return {
        "id": model.get("id", ""),
        "name": model.get("name", ""),
        "base_url": model.get("base_url", ""),
        "model": model.get("model", ""),
        "has_key": bool(encrypted),
        "key_hint": f"…{decrypt_value(encrypted)[-4:]}" if encrypted else "",
        "context_window": int(model.get("context_window") or 0),
        "max_output": int(model.get("max_output") or 0),
        "temperature": float(model.get("temperature") or 0.7),
        "price_in": float(model.get("price_in") or 0),
        "price_out": float(model.get("price_out") or 0),
        "kind": model.get("kind", "chat"),
        "is_default": bool(model.get("is_default")),
        "enabled": bool(model.get("enabled", True)),
        "tags": model.get("tags") or [],
        "note": model.get("note", ""),
        "last_status": model.get("last_status", "unknown"),
        "last_error": model.get("last_error", ""),
        "last_checked_at": model.get("last_checked_at", ""),
        "owner_id": model.get("owner_id", ""),
        "workspace_id": model.get("workspace_id", "default"),
        "created_at": model.get("created_at", ""),
        "updated_at": model.get("updated_at", ""),
    }


def env_payload() -> dict:
    """`.env` 那一档也当成一个「模型」展示，避免页面上出现「无默认模型」的空档。"""
    settings = get_settings()
    resolved = _from_env()
    return {
        "id": "",
        "name": f"环境变量默认（{resolved.model}）",
        "base_url": resolved.base_url,
        "model": resolved.model,
        "has_key": bool(settings.llm_api_key),
        "key_hint": "",
        "context_window": 0,
        "max_output": 0,
        "temperature": float(getattr(settings, "llm_temperature", 0.7) or 0.7),
        "price_in": 0,
        "price_out": 0,
        "kind": "chat",
        "is_default": default_model() is None,
        "enabled": True,
        "tags": ["env"],
        "note": "来自 .env 的 LLM_BASE_URL / LLM_MODEL，只读",
        "last_status": "unknown",
        "last_error": "",
        "last_checked_at": "",
        "owner_id": "",
        "workspace_id": "default",
        "created_at": "",
        "updated_at": "",
        "readonly": True,
    }
