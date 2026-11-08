"""数据库连接与执行层。

一份 SQL 同时跑 SQLite 与 PostgreSQL：
- 业务 SQL 统一用 `?` 占位，Postgres 方言在驱动层替换为 `%s`（本项目 SQL 不含 `?` 字面量）
- 返回结果统一是 `list[dict]`，不依赖任何 ORM

为什么不引 SQLAlchemy：现有模型是 dataclass，上 ORM 要重写全部模型与调用点，
而原生 SQL 的迁移成本只有这张薄封装，且行为完全可控。
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.core.config import get_settings

Params = Sequence[Any] | dict[str, Any]


class DatabaseError(RuntimeError):
    pass


class Database:
    """按 dialect 提供统一读写接口。线程安全：内部串行化写操作。"""

    def __init__(self, dialect: str, dsn: str) -> None:
        if dialect not in ("sqlite", "postgres"):
            raise DatabaseError(f"未知 dialect: {dialect}")
        self.dialect = dialect
        self.dsn = dsn
        self._local = threading.local()
        self._write_lock = threading.Lock()

    # ---------- 连接 ----------
    def _connect(self):  # noqa: ANN202 — 返回 sqlite3.Connection | psycopg.Connection
        if self.dialect == "sqlite":
            path = Path(self.dsn)
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=30000")
            return conn
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=10)

    @property
    def conn(self):  # noqa: ANN201
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        elif self.dialect == "postgres" and conn.closed:
            # 长连接被服务端断开后自动重连，避免整站 500
            conn = self._connect()
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            finally:
                self._local.conn = None

    # ---------- SQL 方言 ----------
    def _adapt(self, sql: str) -> str:
        if self.dialect == "postgres":
            return sql.replace("?", "%s")
        return sql

    # ---------- 读 ----------
    def query(self, sql: str, params: Params = ()) -> list[dict[str, Any]]:
        cur = self.conn.cursor()
        try:
            cur.execute(self._adapt(sql), params)
            rows = cur.fetchall()
        except Exception:
            # 读失败也必须回滚，否则 PG 事务停留在 aborted 状态，毒化后续所有请求
            if self.dialect == "postgres":
                self.conn.rollback()
            raise
        finally:
            cur.close()
        if self.dialect == "sqlite":
            return [dict(r) for r in rows]
        return [dict(r) for r in rows]

    def query_one(self, sql: str, params: Params = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: Params = ()) -> Any:
        row = self.query_one(sql, params)
        if not row:
            return None
        return next(iter(row.values()))

    # ---------- 写 ----------
    def execute(self, sql: str, params: Params = ()) -> int:
        in_tx = getattr(self._local, "in_tx", False)
        if in_tx:
            cur = self.conn.cursor()
            try:
                cur.execute(self._adapt(sql), params)
                return cur.rowcount
            finally:
                cur.close()
        with self._write_lock:
            cur = self.conn.cursor()
            try:
                cur.execute(self._adapt(sql), params)
                affected = cur.rowcount
                self.conn.commit()
                return affected
            except Exception:
                self.conn.rollback()
                raise
            finally:
                cur.close()

    def executemany(self, sql: str, rows: Sequence[Params]) -> int:
        if not rows:
            return 0
        in_tx = getattr(self._local, "in_tx", False)
        if in_tx:
            cur = self.conn.cursor()
            try:
                cur.executemany(self._adapt(sql), rows)
                return cur.rowcount
            finally:
                cur.close()
        with self._write_lock:
            cur = self.conn.cursor()
            try:
                cur.executemany(self._adapt(sql), rows)
                affected = cur.rowcount
                self.conn.commit()
                return affected
            except Exception:
                self.conn.rollback()
                raise
            finally:
                cur.close()

    @contextmanager
    def transaction(self) -> Iterator["Database"]:
        """多语句原子写：块内的 execute/executemany 不再各自 commit。"""
        with self._write_lock:
            conn = self.conn
            self._local.in_tx = True
            try:
                yield self
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                self._local.in_tx = False

    def execute_ddl(self, statements: list[str]) -> None:
        """DDL 批量执行（建表/建索引），不参与事务回滚差异。"""
        with self._write_lock:
            cur = self.conn.cursor()
            try:
                for stmt in statements:
                    cur.execute(stmt)
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
            finally:
                cur.close()


_db: Database | None = None
_db_lock = threading.Lock()


def get_db() -> Database:
    """按配置返回数据库；store_backend=json 时返回 sqlite（内存态不落库，仅供降级调用）。"""
    global _db
    with _db_lock:
        if _db is not None:
            return _db
        s = get_settings()
        if s.store_backend == "postgres":
            _db = Database("postgres", s.postgres_dsn)
        else:
            # sqlite 同时充当 json 后端的兼容落点（迁移脚本可独立指定）
            _db = Database("sqlite", s.sqlite_path)
        return _db


def reset_db() -> None:
    """测试用：丢弃单例，强制下次重新连接。"""
    global _db
    with _db_lock:
        if _db is not None:
            _db.close()
        _db = None
