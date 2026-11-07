"""会话域三后端一致性验收：json / sqlite / postgres 跑同一套断言。

验收门槛与知识库域相同：不是"能连上"，而是"换后端后行为完全一致"。
重点覆盖 v0.1 踩过的坑：
1. 消息必须带顺序（老实现靠 list 位置，换到 SQL 后丢序）
2. 列表页的 message_count 不能触发全量消息加载
3. 老数据的 session_id 为空时，run 自身 id 即会话 id
4. 删会话要级联删消息，不能留孤儿

用法：
    ./.venv/Scripts/python.exe tools/verify_history_backends.py
    ./.venv/Scripts/python.exe tools/verify_history_backends.py --only sqlite --verbose
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import traceback
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.engine import Database  # noqa: E402
from app.db.schema import migrate  # noqa: E402
from app.services.history import HistoryStore  # noqa: E402
from app.services.repositories.history import (  # noqa: E402
    JsonHistoryRepository,
    SqlHistoryRepository,
)

RESULTS: list[tuple[str, str, bool, str]] = []


def check(backend: str, name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((backend, name, ok, detail))
    print(f"{'OK  ' if ok else 'FAIL'} [{backend}] {name}" + (f" · {detail}" if detail else ""))


def run_suite(backend: str, store: HistoryStore, tag: str) -> None:
    # ---- 会话：创建 ----
    cid = f"v-{tag}-{uuid.uuid4().hex[:8]}"
    conv = store.upsert_conversation(cid, "什么是死锁", "四个必要条件……", "死锁讨论", "ws-a")
    check(backend, "建会话返回消息数", conv.message_count == 2, f"{conv.message_count} 条")

    got = store.get_conversation(cid)
    check(backend, "详情可取", got is not None and got.title == "死锁讨论")
    roles = [m.role for m in (got.messages if got else [])]
    check(backend, "消息顺序 user→assistant", roles == ["user", "assistant"], str(roles))
    check(backend, "消息内容不丢", bool(got) and got.messages[0].content == "什么是死锁")

    # ---- 会话：追加一轮 ----
    store.upsert_conversation(cid, "那怎么破坏", "打破循环等待……", "死锁讨论", "ws-a")
    got = store.get_conversation(cid)
    check(backend, "追问追加到同一会话", got is not None and len(got.messages) == 4,
          f"{len(got.messages) if got else 0} 条")
    check(backend, "追加后标题不被覆盖", bool(got) and got.title == "死锁讨论")

    # ---- 列表与隔离 ----
    rows = store.list_conversations("ws-a")
    mine = [c for c in rows if c.id == cid]
    check(backend, "列表命中且计数正确",
          len(mine) == 1 and mine[0].message_count == 4,
          f"count={mine[0].message_count if mine else 'NA'}")
    check(backend, "会话列表不加载正文",
          bool(mine) and mine[0].messages == [], f"messages={len(mine[0].messages) if mine else 'NA'}")
    check(backend, "工作区隔离", all(c.id != cid for c in store.list_conversations("ws-b")))

    # ---- 运行记录：多轮 ----
    session = f"s-{tag}-{uuid.uuid4().hex[:8]}"
    runs = [
        store.add_agent_run(
            task=f"vtest-{tag}-第{i + 1}轮", tool_ids=["code_run"], steps=[{"kind": "tool"}],
            answer=f"答案{i + 1}", status="ok", workspace_id="ws-a",
            project="p1", artifacts=[{"path": "a.md", "kind": "file"}],
            session_id=session, turn=i + 1,
        )
        for i in range(3)
    ]
    check(backend, "运行记录 id 唯一", len({r.id for r in runs}) == 3)
    same = store.list_session_runs(session, "ws-a")
    check(backend, "会话轮次正序", [r.turn for r in same] == [1, 2, 3], str([r.turn for r in same]))
    check(backend, "轮次字段往返",
          bool(same) and same[0].tool_ids == ["code_run"] and same[0].artifacts[0]["path"] == "a.md")
    check(backend, "跨工作区取不到", store.list_session_runs(session, "ws-b") == [])

    # ---- 老数据兼容：session_id 为空 → run.id 即会话 id ----
    legacy = store.add_agent_run(f"vtest-{tag}-老任务", [], [], "老答案", "ok", workspace_id="ws-a")
    legacy_runs = store.list_session_runs(legacy.id, "ws-a")
    check(backend, "老数据按 run.id 聚会话",
          len(legacy_runs) == 1 and legacy_runs[0].id == legacy.id)
    check(backend, "空 session_id 不误命中", store.list_session_runs("", "ws-a") == [])

    # ---- 运行列表排序 ----
    listed = store.list_agent_runs("ws-a")
    check(backend, "运行列表含新记录", all(r.id in {x.id for x in listed} for r in runs))
    check(backend, "运行列表倒序",
          listed[0].created_at >= listed[-1].created_at if listed else False)

    # ---- 删除：单条 / 整会话 ----
    check(backend, "删单条运行", store.delete_agent_run(runs[2].id) and store.get_agent_run(runs[2].id) is None)
    deleted = store.delete_session(session, "ws-a")
    check(backend, "删整会话返回条数", deleted == 2, f"删除 {deleted} 条")
    check(backend, "删会话后无残留",
          store.list_session_runs(session, "ws-a") == [] and store.get_agent_run(runs[0].id) is None)
    store.delete_agent_run(legacy.id)

    # ---- 删除：会话 + 消息级联 ----
    check(backend, "删会话", store.delete_conversation(cid) and store.get_conversation(cid) is None)
    check(backend, "删会话后不在列表", all(c.id != cid for c in store.list_conversations("ws-a")))
    check(backend, "重复删除返回 False", store.delete_conversation(cid) is False)


def build_json(tag: str) -> tuple[HistoryStore, str]:
    tmp = Path(tempfile.gettempdir()) / f"einstein_verify_history_{tag}.json"
    if tmp.exists():
        tmp.unlink()
    return HistoryStore(JsonHistoryRepository(tmp)), str(tmp)


def build_sql(backend: str, dsn: str, tag: str) -> tuple[HistoryStore, str]:
    db = Database(backend, dsn)
    migrate(db)
    # 只清理本套件产生的数据（会话 id 以 "v-" 开头，任务以 "vtest-" 开头）
    stale = [r["id"] for r in db.query("SELECT id FROM conversations WHERE id LIKE ?", ("v-%",))]
    with db.transaction():
        for conv_id in stale:
            db.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
            db.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        db.execute("DELETE FROM agent_runs WHERE task LIKE ?", ("vtest-%",))
    return HistoryStore(SqlHistoryRepository(db)), dsn


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["json", "sqlite", "postgres"], default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    from app.core.config import get_settings

    s = get_settings()
    targets = [args.only] if args.only else ["json", "sqlite", "postgres"]

    for backend in targets:
        tag = backend
        try:
            if backend == "json":
                store, where = build_json(tag)
            elif backend == "sqlite":
                store, where = build_sql("sqlite", s.sqlite_path, tag)
            else:
                store, where = build_sql("postgres", s.postgres_dsn, tag)
            print(f"\n=== {backend} ({where})")
            run_suite(backend, store, tag)
        except Exception as exc:  # noqa: BLE001
            check(backend, "套件执行", False, f"{type(exc).__name__}: {exc}")
            if args.verbose:
                traceback.print_exc()

    total = len(RESULTS)
    failed = [r for r in RESULTS if not r[2]]
    print("\n" + "-" * 70)
    print(f"结果：{total - len(failed)}/{total} 通过")
    for backend, name, _ok, detail in failed:
        print(f"  失败 [{backend}] {name} · {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
