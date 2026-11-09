"""任务模板：把"每周竞品简报"这类固定流程存成一条可复用起点。

模板只固化**起步参数**——任务描述、挂载的知识库与工具、用哪个专家。
执行仍然由智能体自主规划，模板不写死步骤：一旦把流程钉死成脚本，就退化成
工作流引擎，和"智能体自主规划"的定位正面冲突。

可见性沿用三档（private / workspace / shared），默认 private：模板里的任务
描述常常带着具体的项目代号与客户名，默认共享等于默认泄密。
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.db.engine import get_db
from app.db.rows import TableState
from app.db.schema import migrate
from app.db.tables import TASK_TEMPLATES
from app.services.state import JsonState

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_JSON = JsonState(_DATA_DIR / "task_templates.json")
_lock = threading.Lock()

VISIBILITIES = ("private", "workspace", "shared")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _use_table() -> bool:
    return get_settings().store_backend != "json"


def _table() -> TableState:
    db = get_db()
    migrate(db)
    return TableState(TASK_TEMPLATES, db)


def _all() -> list[dict]:
    if _use_table():
        return _table().rows()
    return [r for r in (_JSON.read() or []) if isinstance(r, dict)]


def _save_all(rows: list[dict]) -> None:
    if _use_table():
        _table().write({r["id"]: r for r in rows})
    else:
        _JSON.write(rows)


def visible(tpl: dict, workspace_id: str, owner_id: str) -> bool:
    if tpl.get("workspace_id") != workspace_id and tpl.get("visibility") != "shared":
        return False
    if tpl.get("owner_id") in ("", owner_id):
        return True
    return tpl.get("visibility") in ("workspace", "shared")


def list_templates(workspace_id: str, owner_id: str) -> list[dict]:
    rows = [t for t in _all() if visible(t, workspace_id, owner_id)]
    # 常用的排前面：模板的价值在于"第二次更快"，用得多说明它真被用了。
    # sort 是稳定的，同使用次数内保留表本身的 updated_at DESC 顺序（新的在前）。
    rows.sort(key=lambda t: -int(t.get("use_count") or 0))
    return rows


def get_template(template_id: str) -> dict | None:
    return next((t for t in _all() if t.get("id") == template_id), None)


def create_template(
    *, workspace_id: str, owner_id: str, name: str, prompt: str,
    description: str = "", expert_id: str = "", project: str = "",
    tool_ids: list[str] | None = None, kb_ids: list[str] | None = None,
    visibility: str = "private",
) -> dict:
    if not (name or "").strip():
        raise ValueError("模板名称不能为空")
    if not (prompt or "").strip():
        raise ValueError("任务描述不能为空")
    if visibility not in VISIBILITIES:
        raise ValueError(f"visibility 只能是 {'/'.join(VISIBILITIES)}")
    entry = {
        "id": uuid.uuid4().hex[:12],
        "workspace_id": workspace_id,
        "owner_id": owner_id,
        "name": name.strip(),
        "description": description,
        "prompt": prompt,
        "expert_id": expert_id,
        "project": project,
        "tool_ids": list(tool_ids or []),
        "kb_ids": list(kb_ids or []),
        "visibility": visibility,
        "use_count": 0,
        "created_at": _now(),
        "updated_at": _now(),
    }
    with _lock:
        rows = _all()
        rows.append(entry)
        _save_all(rows)
    return entry


_UPDATABLE = {
    "name", "description", "prompt", "expert_id", "project",
    "tool_ids", "kb_ids", "visibility",
}


def update_template(template_id: str, patch: dict) -> dict | None:
    with _lock:
        rows = _all()
        entry = next((t for t in rows if t.get("id") == template_id), None)
        if entry is None:
            return None
        for key, value in patch.items():
            if key in _UPDATABLE and value is not None:
                entry[key] = value
        if entry.get("visibility") not in VISIBILITIES:
            raise ValueError(f"visibility 只能是 {'/'.join(VISIBILITIES)}")
        entry["updated_at"] = _now()
        _save_all(rows)
    return entry


def delete_template(template_id: str) -> bool:
    with _lock:
        rows = _all()
        kept = [t for t in rows if t.get("id") != template_id]
        if len(kept) == len(rows):
            return False
        _save_all(kept)
        return True


def touch(template_id: str) -> dict | None:
    """记一次使用。用独立的写路径，不走用户可改字段白名单。"""
    with _lock:
        rows = _all()
        entry = next((t for t in rows if t.get("id") == template_id), None)
        if entry is None:
            return None
        entry["use_count"] = int(entry.get("use_count") or 0) + 1
        _save_all(rows)
    return entry


def can_edit(tpl: dict, owner_id: str, is_admin: bool = False) -> bool:
    owner = tpl.get("owner_id", "")
    if owner == owner_id and owner != "":
        return True
    return owner == "" and is_admin


def payload(tpl: dict) -> dict:
    return {
        "id": tpl.get("id", ""),
        "name": tpl.get("name", ""),
        "description": tpl.get("description", ""),
        "prompt": tpl.get("prompt", ""),
        "expert_id": tpl.get("expert_id", ""),
        "project": tpl.get("project", ""),
        "tool_ids": tpl.get("tool_ids") or [],
        "kb_ids": tpl.get("kb_ids") or [],
        "visibility": tpl.get("visibility", "private"),
        "use_count": int(tpl.get("use_count") or 0),
        "owner_id": tpl.get("owner_id", ""),
        "workspace_id": tpl.get("workspace_id", "default"),
        "created_at": tpl.get("created_at", ""),
        "updated_at": tpl.get("updated_at", ""),
    }
