"""项目：用户可见的产物目录，一个有归属的资源。

问题和修法
----------
早先是「目录即项目」：`data/projects/<workspace>/` 下有什么目录，界面就列
什么。可这个根目录同时住着**运行时目录**——技能同步目录、临时计算沙盒——
于是 `skills/` 被当成项目出现在「文件」里，用户点进去看到的是平台自己的技能
文件；更糟的是项目没有归属，同一空间的两个用户互相看得见对方的项目。

现在项目是**先登记再创建**的资源：
- 只有 `projects` 表里存在、且 `owner_id` 是当前用户的行才出现在列表里；
- `internal=1` 的是运行时目录（如 `_scratch`），只给智能体用，永不展示；
- 磁盘目录降级为「落点」，`sandbox` 只管文件，项目语义都在这里。

归属语义与专家/技能不同：项目没有「系统预置」这一档，每个项目都属于某个人，
空间管理员也看不到别人的项目（和个人空间的边界一致）。
"""

from __future__ import annotations

import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.db.engine import get_db
from app.db.rows import TableState
from app.db.schema import migrate
from app.db.tables import PROJECTS
from app.services.capabilities import sandbox

# 运行时目录名：历史上技能同步目录叫 `skills`，现在在 `.agent/skills`。
# 两个名字都不允许被当成项目（老库里可能还留着 `skills/` 残留目录）。
RESERVED_NAMES = {"skills"}
# 下划线/点开头是内部目录（_scratch、.agent、.deckpreview…），不对用户展示
_INTERNAL_PREFIXES = ("_", ".")

_lock = threading.Lock()
_table: TableState | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _state() -> TableState:
    global _table
    if _table is None:
        db = get_db()
        migrate(db)
        _table = TableState(PROJECTS, db)
    return _table


def is_internal_name(slug: str) -> bool:
    """内部目录判定：下划线/点开头，或历史运行时目录名。"""
    return not slug or slug.startswith(_INTERNAL_PREFIXES) or slug in RESERVED_NAMES


# ---------------------------------------------------------------- 读


def _rows_of(workspace_id: str) -> list[dict]:
    return _state().rows("workspace_id = ?", (workspace_id,))


def get(workspace_id: str, slug: str) -> dict | None:
    for row in _rows_of(workspace_id):
        if row.get("slug") == slug:
            return row
    return None


def visible(row: dict, owner_id: str | None) -> bool:
    """归属判定：项目永远属于某个人；owner_id=None 表示内部调用不设限。

    `owner_id == ''` 的老数据（收养失败等）按「空间共享」处理，只在没有更
    精确归属时兜底，避免项目凭空消失。
    """
    if owner_id is None:
        return True
    return row.get("owner_id", "") in ("", owner_id)


def owns(workspace_id: str, slug: str, owner_id: str | None) -> bool:
    row = get(workspace_id, slug)
    return bool(row) and visible(row, owner_id)


def list_projects(workspace_id: str, owner_id: str | None = None) -> list[dict]:
    """当前用户可见的项目（含文件数/体积，按最后改动排序）。

    只认登记过的行：磁盘上多出来的目录（运行时目录、历史残留、别人手工
    建的目录）不会因为「存在」就变成项目。
    """
    root = sandbox.workspace_root(workspace_id)
    out: list[dict] = []
    for row in _rows_of(workspace_id):
        if row.get("internal") or is_internal_name(row.get("slug", "")):
            continue
        if not visible(row, owner_id):
            continue
        path = root / row["slug"]
        if not path.is_dir():
            continue  # 元数据还在、目录已被手工删除：不展示不存在的项目
        stat = path.stat()
        out.append(
            {
                "id": row["slug"],
                "name": row.get("name") or row["slug"],
                "description": row.get("description", ""),
                "template": row.get("template", "blank"),
                "owner_id": row.get("owner_id", ""),
                "files": sum(1 for f in path.rglob("*") if f.is_file()),
                "size": sandbox.human_size(sandbox.dir_size(path)),
                "created_at": row.get("created_at", ""),
                "updated_at": _local_time(stat.st_mtime),
                "mtime": stat.st_mtime,
            }
        )
    out.sort(key=lambda item: item["mtime"], reverse=True)
    for item in out:
        item.pop("mtime", None)
    return out


def _local_time(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def require(workspace_id: str, slug: str, owner_id: str | None = None) -> Path:
    """取项目目录，顺带做归属校验；不可见时抛「不存在」（不泄露存在性）。"""
    row = get(workspace_id, slug)
    if row is None or not visible(row, owner_id) or row.get("internal"):
        raise sandbox.SandboxError(f"项目 {slug} 不存在")
    return sandbox.project_dir(workspace_id, slug)


def resolve_rel(workspace_id: str, slug: str, rel: str, owner_id: str | None = None) -> Path:
    root = require(workspace_id, slug, owner_id)
    return sandbox.resolve_in(root, rel)


# ---------------------------------------------------------------- 写


def create_project(
    workspace_id: str,
    name: str,
    template: str = "blank",
    description: str = "",
    *,
    owner_id: str = "",
    internal: bool = False,
) -> dict:
    """创建（或复用）项目并登记归属。目录已存在时只补文件、不改归属。"""
    slug = sandbox.slug_from_name(name)
    if not slug:
        # 以前空值回落 untitled：所有"名字不可用"的项目挤进同一个目录，
        # 第二次创建直接复用第一次的目录，用户以为项目串了。
        raise sandbox.SandboxError(
            "项目名需要包含中英文、数字、下划线或连字符（不能只有表情或符号）"
        )
    if slug in sandbox.RESERVED:
        raise sandbox.SandboxError("项目名无效")
    if not internal and is_internal_name(slug):
        raise sandbox.SandboxError(f"项目名不能以下划线或点开头，也不能用 {sorted(RESERVED_NAMES)} 这类保留名")

    info = sandbox.scaffold_project(workspace_id, slug, template, description)
    now = _now()
    row = get(workspace_id, slug)
    with _lock:
        if row is None:
            _state().upsert(
                {
                    "workspace_id": workspace_id,
                    "slug": slug,
                    "owner_id": owner_id,
                    "name": (name or "").strip() or slug,
                    "description": description.strip(),
                    "template": template,
                    "internal": internal,
                    "created_at": now,
                    "updated_at": now,
                }
            )
        elif row.get("internal") and not internal:
            # 内部目录（_scratch 等）被当成项目名复用：拒绝，避免把运行时目录
            # 暴露成用户的普通项目
            raise sandbox.SandboxError(f"项目 {slug} 是系统内部目录，不能作为普通项目使用")
        else:
            _state().upsert({**row, "updated_at": now})
    info.update({"name": (name or "").strip() or slug, "owner_id": owner_id if row is None else row.get("owner_id", "")})
    return info


def ensure_internal(workspace_id: str, slug: str, owner_id: str, description: str = "") -> str:
    """确保运行时目录存在（不展示、不参与配额），返回 slug。"""
    row = get(workspace_id, slug)
    if row is None:
        create_project(
            workspace_id, slug, "blank", description, owner_id=owner_id, internal=True
        )
    return slug


def delete_project(workspace_id: str, slug: str, owner_id: str | None = None) -> bool:
    require(workspace_id, slug, owner_id)
    root = sandbox.project_dir(workspace_id, slug)
    shutil.rmtree(root, ignore_errors=True)
    with _lock:
        _state().delete_where("workspace_id = ? AND slug = ?", (workspace_id, slug))
    return not root.exists()


def drop_workspace(workspace_id: str) -> int:
    """工作区被删除时清掉它的项目登记（目录由调用方处理）。"""
    with _lock:
        return _state().delete_where("workspace_id = ?", (workspace_id,))


# ---------------------------------------------------------------- 存量收养


def adopt_orphans() -> dict:
    """把「磁盘上有目录、表里没登记」的存量项目登记进来。

    幂等，启动时跑一次。归属取空间所有者（个人空间就是空间主人自己），
    宁可收紧也不外泄：收养失败宁可不登记（项目暂时不可见），也不能默认
    共享给全空间。
    """
    from app.services import members, workspaces

    adopted = 0
    skipped = 0
    registry = workspaces.get_workspace_store()
    member_store = members.get_member_store()
    for space in registry.list():
        root = sandbox.workspace_root(space.id)
        if not root.is_dir():
            continue
        owner = next(
            (
                m.user_id
                for m in member_store.members_of(space.id)
                if m.role == "owner"
            ),
            "",
        )
        known = {row.get("slug") for row in _rows_of(space.id)}
        for path in sorted(root.iterdir()):
            if not path.is_dir() or path.name in known:
                continue
            if is_internal_name(path.name):
                skipped += 1  # 运行时目录：不是项目，不登记
                continue
            with _lock:
                _state().upsert(
                    {
                        "workspace_id": space.id,
                        "slug": path.name,
                        "owner_id": owner,
                        "name": path.name,
                        "description": "",
                        "template": "blank",
                        "internal": False,
                        "created_at": _local_time(path.stat().st_mtime),
                        "updated_at": _local_time(path.stat().st_mtime),
                    }
                )
            adopted += 1
    return {"adopted": adopted, "skipped": skipped}


def stats(workspace_id: str | None = None) -> dict:
    """自检用：登记数 / 内部目录数 / 各归属的项目数。"""
    rows = _state().rows("workspace_id = ?" if workspace_id else "", (workspace_id,) if workspace_id else ())
    by_owner: dict[str, int] = {}
    internal = 0
    for row in rows:
        if row.get("internal"):
            internal += 1
            continue
        by_owner[row.get("owner_id", "")] = by_owner.get(row.get("owner_id", ""), 0) + 1
    return {"total": len(rows), "internal": internal, "by_owner": by_owner}
