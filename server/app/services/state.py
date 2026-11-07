"""配置域状态后端。

专家、技能、用户、工作区、API Key、审计、评测集、MCP 服务器这些域的数据形状是
"整域一份 JSON"，历史实现各自读写 `data/*.json`。多实例部署时这份状态会分叉，
所以这里把它抽象成一个可切换的后端：

- `json`   → 仍然读写原来的 `data/*.json`，向后完全兼容，可手改、可 diff
- `sqlite` / `postgres` → 写 `domain_state` 表（domain 主键 + payload + version）

写用单条 SQL 原子覆盖（文件后端用临时文件 + 原子替换）：两个实例同时改同一域时
是"最后写入者胜"，但不会写出半截 JSON。配置类域（专家、技能、MCP、API Key）
写频率极低且由管理员操作，这个语义足够；真正的行级并发要等 S2.4 把域拆成表。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from app.core.config import get_settings
from app.db.engine import Database, get_db
from app.db.schema import migrate

__all__ = ["StateBackend", "StateConflict", "JsonState", "SqlState", "blob"]


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
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        with _json_lock(self.path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.path)


class SqlState:
    """数据库后端：一个域一行，payload 存 JSON 文本。"""

    def __init__(self, db: Database, domain: str) -> None:
        self.db = db
        self.domain = domain

    def read(self) -> Any | None:
        rows = self.db.query("SELECT payload FROM domain_state WHERE domain = ?", (self.domain,))
        if not rows:
            return None
        try:
            return json.loads(rows[0]["payload"])
        except Exception:  # noqa: BLE001
            return None

    def write(self, payload: Any) -> None:
        text = json.dumps(payload, ensure_ascii=False)
        now = datetime.now(timezone.utc).isoformat()
        for _ in range(5):
            rows = self.db.query(
                "SELECT version FROM domain_state WHERE domain = ?", (self.domain,)
            )
            if rows:
                version = int(rows[0]["version"])
                changed = self.db.execute(
                    "UPDATE domain_state SET payload = ?, version = ?, updated_at = ?"
                    " WHERE domain = ? AND version = ?",
                    (text, version + 1, now, self.domain, version),
                )
                if changed == 1:
                    return
                continue
            try:
                self.db.execute(
                    "INSERT INTO domain_state (domain, payload, version, updated_at)"
                    " VALUES (?, ?, ?, ?)",
                    (self.domain, text, 1, now),
                )
                return
            except Exception:  # noqa: BLE001 — 并发插入，回到循环走 UPDATE
                continue
        raise StateConflict(f"{self.domain} 状态写入冲突，请重试")


_ensured: set[str] = set()


def _ensure_schema(db: Database) -> None:
    key = f"{db.dialect}:{db.dsn}"
    if key in _ensured:
        return
    migrate(db)
    _ensured.add(key)


def blob(domain: str, json_path: Path) -> StateBackend:
    """按当前后端返回该域的状态读写器。"""
    settings = get_settings()
    if settings.store_backend == "json":
        return JsonState(json_path)
    db = get_db()
    _ensure_schema(db)
    return SqlState(db, domain)
