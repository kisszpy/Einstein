"""按表规范读写行：一次只动一行，不再整域覆盖。

`TableState` 保留老的「整域字典」视角（`read()` / `write(payload)`），
但内部按主键做增量落库：

    写入前快照: {键: (行参数…)}  写入后: 新增 → INSERT，有变 → UPDATE，消失 → DELETE

这样各域 store 的调用方式不用改（仍是「内存字典 + save()」），
而数据库里是真的行：审计追加一条只 INSERT 一行，改一个专家只 UPDATE 一行，
两个实例改不同行也不再互相覆盖整域。
"""

from __future__ import annotations

import json
import threading
from typing import Any

from app.db.engine import Database
from app.db.tables import Column, Table

__all__ = ["TableState", "to_db", "from_db", "row_values", "row_pk"]


# ---------------------------------------------------------------- 类型转换


def to_db(col: Column, value: Any) -> Any:
    """字段值 → 数据库参数（json 列序列化；None 在非空列上收敛成默认值）。"""
    if col.kind == "json":
        if value is None:
            value = [] if col.default == "'[]'" else {}
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if value is None:
        if col.nullable:
            return None
        return {"int": 0, "real": 0.0, "bool": False}.get(col.kind, "")
    if col.kind == "bool":
        return bool(value)
    if col.kind == "int":
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
    if col.kind == "real":
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
    return str(value)


def from_db(col: Column, value: Any) -> Any:
    """数据库值 → 字段值（bool 归一，json 反序列化，可空列保留 None）。"""
    if value is None:
        return None if col.nullable else {"int": 0, "real": 0.0, "bool": False}.get(col.kind, "")
    if col.kind == "json":
        if isinstance(value, (list, dict)):
            return value
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return [] if col.default == "'[]'" else {}
    if col.kind == "bool":
        return bool(value)
    if col.kind == "int":
        return int(value)
    if col.kind == "real":
        return float(value)
    return str(value)


def row_values(table: Table, row: dict) -> tuple[Any, ...]:
    """按列的声明顺序把一行转成参数元组；缺字段取列默认值。"""
    return tuple(to_db(col, row.get(col.attr)) for col in table.columns)


def row_pk(table: Table, row: dict) -> tuple[Any, ...]:
    return tuple(
        to_db(table.column(name), row.get(table.column(name).attr)) for name in table.key
    )


def key_of(table: Table, row: dict) -> str:
    """字典视角的键：单主键=主键值；复合主键按 key_sep 拼接（如 user@ws）。"""
    parts = [str(row.get(table.column(name).attr, "")) for name in table.key]
    return table.key_sep.join(parts) if table.key_sep else "".join(parts)


def _where_pk(table: Table) -> str:
    return " AND ".join(f"{name} = ?" for name in table.key)


def _pk_params(table: Table, row: dict) -> tuple[Any, ...]:
    return tuple(
        to_db(table.column(name), row.get(table.column(name).attr)) for name in table.key
    )


# ---------------------------------------------------------------- 行级读写


class TableState:
    """一张表的读写器，接口与 JSON 后端一致（read / write）。"""

    def __init__(self, table: Table, db: Database) -> None:
        self.table = table
        self.db = db
        # 上次读/写后的快照：键 → (行参数, 行字典)，用于算增量
        self._snapshot: dict[str, tuple[tuple[Any, ...], dict]] | None = None
        self._lock = threading.RLock()

    # ---------- 读 ----------

    def rows(
        self,
        where: str = "",
        params: tuple = (),
        *,
        limit: int | None = None,
        order: str | None = None,
    ) -> list[dict]:
        cols = ", ".join(c.name for c in self.table.columns)
        sql = f"SELECT {cols} FROM {self.table.name}"
        if where:
            sql += f" WHERE {where}"
        sort = order if order is not None else self.table.order
        if sort:
            sql += f" ORDER BY {sort}"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [
            {col.attr: from_db(col, raw[col.name]) for col in self.table.columns}
            for raw in self.db.query(sql, params)
        ]

    def read(self) -> dict[str, dict] | None:
        rows = self.rows()
        self._snapshot = {
            key_of(self.table, row): (row_values(self.table, row), row) for row in rows
        }
        if not rows:
            return None
        return {key: row for key, (_values, row) in self._snapshot.items()}

    def count(self) -> int:
        return int(self.db.scalar(f"SELECT count(*) AS n FROM {self.table.name}") or 0)

    # ---------- 写 ----------

    def _insert(self, row: dict) -> None:
        cols = ", ".join(c.name for c in self.table.columns)
        marks = ", ".join("?" for _ in self.table.columns)
        self.db.execute(
            f"INSERT INTO {self.table.name} ({cols}) VALUES ({marks})", row_values(self.table, row)
        )

    def _update(self, row: dict) -> None:
        mutable = [c for c in self.table.columns if c.name not in self.table.key]
        sets = ", ".join(f"{c.name} = ?" for c in mutable)
        params = tuple(to_db(c, row.get(c.attr)) for c in mutable) + _pk_params(self.table, row)
        self.db.execute(
            f"UPDATE {self.table.name} SET {sets} WHERE {_where_pk(self.table)}", params
        )
    def _delete(self, row: dict) -> None:
        self.db.execute(
            f"DELETE FROM {self.table.name} WHERE {_where_pk(self.table)}", _pk_params(self.table, row)
        )

    def _normalize(self, payload: dict) -> dict[str, dict]:
        """补齐主键字段：复合主键的历史字典键是 "user@ws"，缺字段时按分隔符还原。"""
        out: dict[str, dict] = {}
        for key, value in payload.items():
            row = dict(value) if isinstance(value, dict) else {}
            missing = [n for n in self.table.key if not row.get(n)]
            if missing and self.table.key_sep:
                parts = str(key).split(self.table.key_sep)
                if len(parts) == len(self.table.key):
                    for name, part in zip(self.table.key, parts):
                        row.setdefault(name, part)
            for name in self.table.key:
                row.setdefault(name, str(key) if len(self.table.key) == 1 else "")
            out[key] = row
        return out

    def write(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            raise TypeError(f"{self.table.name} 按主键增量写入，payload 必须是 dict[str, dict]")
        rows = self._normalize(payload)
        with self._lock:
            before = self._snapshot if self._snapshot is not None else self._load_snapshot()
            after = {k: (row_values(self.table, r), r) for k, r in rows.items()}
            inserts = [k for k in after if k not in before]
            updates = [k for k in after if k in before and after[k][0] != before[k][0]]
            deletes = [k for k in before if k not in after]
            with self.db.transaction():
                for key in inserts:
                    self._insert(after[key][1])
                for key in updates:
                    self._update(after[key][1])
                for key in deletes:
                    self._delete(before[key][1])
            self._snapshot = after

    def _load_snapshot(self) -> dict[str, tuple[tuple[Any, ...], dict]]:
        return {
            key_of(self.table, row): (row_values(self.table, row), row) for row in self.rows()
        }

    # ---------- 单行操作（日志类域直接用） ----------

    def upsert(self, row: dict) -> None:
        exists = self.db.scalar(
            f"SELECT count(*) AS n FROM {self.table.name} WHERE {_where_pk(self.table)}",
            _pk_params(self.table, row),
        )
        if exists:
            self._update(row)
        else:
            self._insert(row)
        if self._snapshot is not None:
            self._snapshot[key_of(self.table, row)] = (row_values(self.table, row), row)

    def delete_where(self, where: str, params: tuple = ()) -> int:
        changed = self.db.execute(f"DELETE FROM {self.table.name} WHERE {where}", params)
        self._snapshot = None  # 外部改动后快照失效，下次写入前重新读
        return changed

    def insert_many(self, items: list[dict]) -> int:
        if not items:
            return 0
        cols = ", ".join(c.name for c in self.table.columns)
        marks = ", ".join("?" for _ in self.table.columns)
        return self.db.executemany(
            f"INSERT INTO {self.table.name} ({cols}) VALUES ({marks})",
            [row_values(self.table, r) for r in items],
        )
