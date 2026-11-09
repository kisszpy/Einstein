"""配置域状态的读写入口。

历史：专家、技能、用户、工作区、API Key、审计这些域的数据形状是
"整域一份 JSON"，最早各自读写 `data/*.json`；多实例部署时会分叉，于是被
抽象成 `blob(domain, path)`，数据库侧统一塞进 `domain_state(domain, payload)`。
那张表是个过渡方案——一域一行、一个字段都看不见，改一行要重写整域。

现在按域拆成真表（规范见 `app/db/tables.py`）：

- `json`     → 仍读写原来的 `data/*.json`（本地无库时的降级路径，可手改可 diff）
- `sqlite` / `postgres` → 每个域一张表，一个字段一列，按主键做行级增量写

两层实现都满足同一个 `StateBackend` 协议（read / write），所以各域 store
（`users.py` / `experts.py` …）只认协议、不关心后端。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Protocol

from app.core.config import get_settings
from app.db.engine import Database, get_db
from app.db.rows import TableState
from app.db.schema import migrate
from app.db.tables import table_of

__all__ = ["StateBackend", "StateConflict", "JsonState", "TableState", "blob", "table_of"]


class StateConflict(RuntimeError):
    """写入重试多次仍失败（数据库不可用或持续冲突）。"""


class StateBackend(Protocol):
    def read(self) -> Any | None: ...

    def write(self, payload: Any) -> None: ...


_JSON_LOCKS: dict[str, threading.Lock] = {}
_JSON_GUARD = threading.Lock()


def _json_lock(path: Path) -> threading.Lock:
    """Windows 上并发写同一文件会报 PermissionError，按路径加锁串行化。"""
    key = str(path)
    with _JSON_GUARD:
        lock = _JSON_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _JSON_LOCKS[key] = lock
        return lock


class JsonState:
    """单文件后端：保持 v0.1 的文件名与缩进，便于人工查看。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> Any | None:
        with _json_lock(self.path):
            if not self.path.exists():
                return None
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 — 损坏按空处理，由调用方重建
                return None

    def write(self, payload: Any) -> None:
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        with _json_lock(self.path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.path)


_ensured: set[str] = set()


def _ensure_schema(db: Database) -> None:
    """建表 + 一次性的存量拆表。

    必须在任何 store 读数据之前完成：否则新表是空的，`UserStore.load()`
    会以为库里没账号而重新种一个 admin，随后存量导入又因为"目标表非空"
    而跳过——真数据就被空表挡在门外了。
    """
    key = f"{db.dialect}:{db.dsn}"
    if key in _ensured:
        return
    migrate(db)
    from app.db.legacy import import_domain_state, import_json_backed

    import_domain_state(db)
    # 会话与邀请码原本单独放 JSON 文件：不搬的话所有人被登出、已发的邀请码失效
    import_json_backed(db, Path(__file__).resolve().parents[2] / "data")
    _ensured.add(key)


def blob(domain: str, json_path: Path) -> StateBackend:
    """按当前后端返回该域的状态读写器。

    未登记的域直接报错：宁可启动失败，也不要再退回"整域塞一列"的老路。
    """
    settings = get_settings()
    if settings.store_backend == "json":
        return JsonState(json_path)
    table = table_of(domain)
    if table is None:
        raise KeyError(f"域 {domain} 没有对应的表，请在 app/db/tables.py 登记")
    db = get_db()
    _ensure_schema(db)
    return TableState(table, db)
