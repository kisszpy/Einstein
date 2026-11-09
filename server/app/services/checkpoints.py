"""短期记忆（LangGraph 线程状态）的落盘。

**为什么必须落盘**：人工确认在框架里的语义是 `interrupt`——图暂停后靠
`Command(resume=...)` 读同一个 `thread_id` 的检查点续跑。检查点只活在进程
内存里时，服务一重启续跑就退化成「按批准重跑」：已写出的文件、已花掉的
token 全部重做一遍（`agent_deep.py` 会明确告知，不会假装续跑）。

四档（`APP_AGENT_CHECKPOINT`）：

| 值 | 行为 |
|---|---|
| `auto`（默认） | 跟主存储走：Linux 上主库 postgres 就用 postgres，其余用 sqlite |
| `sqlite` | 固定落 `APP_AGENT_CHECKPOINT_SQLITE_PATH`（默认 `data/checkpoints.db`） |
| `postgres` | 落 `APP_POSTGRES_DSN` 指向的库（多副本部署必须用这个） |
| `memory` | 只在进程内，快，重启即丢（测试用） |
| `off` | 不保存检查点，等于关掉中断续跑 |

**Windows 上没有 postgres 档**：psycopg 的异步连接要求 selector 事件循环，而
uvicorn 在 Windows 上建的是 Proactor。`auto` 因此按平台直接选 sqlite；显式配
postgres 会立刻降级并说明原因（不会白等连接池超时）。多副本部署请用 Linux。

**为什么不能把 sqlite 档当多副本方案**：`AsyncSqliteSaver` 在构造时绑定了
当前事件循环，而且文件是单机的——多进程/多副本各写各的，续跑请求打到哪个
进程就只能看到哪个进程的检查点。
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"

# 一次清理最多扫多少个检查点。清理是低频操作，全量扫在几千会话下没问题，
# 但要有上限兜住"历史数据特别多"的意外。
SCAN_LIMIT = 20000

_SAVER: Any = None
_MODE = ""
_CLOSERS: list[Any] = []
_LOCK: asyncio.Lock | None = None
# 内存档的单例：同步可构造，供 `_build_agent` 在拿不到异步 saver 时兜底
_MEMORY: Any = None


# ------------------------------------------------------------------ 模式
def psycopg_async_supported() -> bool:
    """当前平台能不能用 psycopg 的异步连接。

    Windows 的默认事件循环是 Proactor，psycopg 明确不支持
    （"Psycopg cannot use the 'ProactorEventLoop' to run in async mode"）。
    uvicorn 在 Windows 上建的就是 Proactor，而 `app.main` 是在 loop 内部
    导入的，模块级改 policy 已经来不及——所以这一档在 Windows 上只能放弃。
    Linux/macOS 是 epoll/kqueue，正常可用。
    """
    return sys.platform != "win32"


def resolve_mode() -> str:
    """把配置解析成实际的存储档位，非法值退回 auto。"""
    raw = (get_settings().agent_checkpoint or "auto").strip().lower()
    if raw in ("off", "none", "0", "false", "no"):
        return "off"
    if raw == "memory":
        return "memory"
    if raw == "sqlite":
        return "sqlite"
    if raw == "postgres":
        # 显式指定时保留用户意图；真连不上会降级并说明原因
        return "postgres"
    # auto：跟主存储走。postgres 主库在 Linux 上配 postgres 检查点（多副本
    # 必须共享），在 Windows 上只能退回 sqlite——否则每次启动白等 15 秒再降级。
    backend = (get_settings().store_backend or "").strip().lower()
    if backend == "postgres" and psycopg_async_supported():
        return "postgres"
    return "sqlite"


def memory_saver() -> Any:
    """进程内 saver（同步构造）。测试与降级路径用。"""
    global _MEMORY
    if _MEMORY is None:
        from langgraph.checkpoint.memory import InMemorySaver

        _MEMORY = InMemorySaver()
    return _MEMORY


def _lock() -> asyncio.Lock:
    """按事件循环取锁。

    模块级的 `asyncio.Lock` 会绑定首次 await 时的 loop，之后换个 loop 用会报
    「bound to a different event loop」——pytest-asyncio 每个用例一个新 loop，
    正好踩到。所以按 loop 分桶。
    """
    global _LOCK
    loop = asyncio.get_running_loop()
    if _LOCK is None or getattr(_LOCK, "_bound", None) is not loop:
        _LOCK = asyncio.Lock()
        _LOCK._bound = loop  # type: ignore[attr-defined]
    return _LOCK


# ------------------------------------------------------------------ 装配
async def acquire() -> Any:
    """按配置取 checkpointer；`off` 返回 None。

    持久化档打不开时**降级到内存**而不是抛错：检查点坏了只该让「续跑」退化，
    不该让整个智能体不可用。
    """
    global _SAVER, _MODE, _CLOSERS

    mode = resolve_mode()
    if mode == "off":
        return None
    if mode == "memory":
        return memory_saver()

    async with _lock():
        if _SAVER is not None and _MODE == mode:
            return _SAVER
        await _reset()
        try:
            if mode == "sqlite":
                _SAVER, _CLOSERS = await _open_sqlite()
            else:
                _SAVER, _CLOSERS = await _open_postgres()
            _MODE = mode
            print(f"[checkpoint] 短期记忆落盘：{mode}（{_target(mode)}）", flush=True)
        except Exception as exc:  # noqa: BLE001 — 降级，不让 agent 不可用
            _SAVER = None
            _MODE = ""
            _CLOSERS = []
            print(
                f"[checkpoint] {mode} 不可用（{type(exc).__name__}: {exc}），尝试 sqlite",
                flush=True,
            )
            # 降级优先 sqlite 而不是内存：内存等于「什么都没落」，用户看不到
            # 任何异常，直到某天批准之后发现任务重跑了一遍。
            if mode != "sqlite":
                try:
                    _SAVER, _CLOSERS = await _open_sqlite()
                    _MODE = "sqlite"
                    print(f"[checkpoint] 已降级为 sqlite（{_sqlite_path()}）", flush=True)
                    return _SAVER
                except Exception as exc2:  # noqa: BLE001
                    print(
                        f"[checkpoint] sqlite 也不可用（{type(exc2).__name__}: {exc2}），"
                        "退回内存：服务重启后无法从断点续跑",
                        flush=True,
                    )
            _SAVER = None
            _MODE = ""
            return memory_saver()
        return _SAVER


def _target(mode: str) -> str:
    if mode == "sqlite":
        return str(_sqlite_path())
    return (get_settings().postgres_dsn or "").split("@")[-1] or "postgres"


def _sqlite_path() -> Path:
    raw = (get_settings().agent_checkpoint_sqlite_path or "").strip()
    return Path(raw) if raw else _DATA_DIR / "checkpoints.db"


async def _open_sqlite() -> tuple[Any, list[Any]]:
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    path = _sqlite_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(str(path))
    saver = AsyncSqliteSaver(conn)
    # 建表由框架自己负责（幂等）；漏了这步第一次检索会报 "no such table"
    await saver.setup()
    return saver, [conn.close]


async def _open_postgres() -> tuple[Any, list[Any]]:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    # 早失败：Windows 上继续试只会白等 15 秒 pool 超时，错误信息还没这句清楚
    if not psycopg_async_supported():
        raise RuntimeError(
            "Windows 的 Proactor 事件循环不支持 psycopg 异步连接；"
            "单机请用 sqlite，多副本请部署在 Linux"
        )

    dsn = (get_settings().postgres_dsn or "").strip()
    if not dsn:
        raise RuntimeError("APP_POSTGRES_DSN 为空")
    pool = AsyncConnectionPool(
        conninfo=dsn,
        max_size=4,
        open=False,
        # autocommit + prepare_threshold=0 是 langgraph 对连接的要求：
        # saver 会自己开事务，驱动再包一层会让 DDL 迁移卡住
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await pool.open(wait=True, timeout=15)
    saver = AsyncPostgresSaver(pool)
    await saver.setup()
    return saver, [pool.close]


async def _reset() -> None:
    """关掉旧的连接（切档或进程退出时）。"""
    global _SAVER, _MODE, _CLOSERS
    for closer in _CLOSERS:
        try:
            result = closer()
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # noqa: BLE001 — 关闭失败不该影响新连接
            pass
    _SAVER = None
    _MODE = ""
    _CLOSERS = []


async def close() -> None:
    await _reset()


# ------------------------------------------------------------------ 清理
def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def purge_expired() -> int:
    """删掉超过 `APP_AGENT_CHECKPOINT_TTL_HOURS` 没更新的线程，返回清理数。

    判据用 checkpoint 自带的 `ts`（该线程最新一条），不是文件/表的时间戳——
    后者会被任何一次写入刷新，永远删不掉东西。

    不能误删「正在等批准」的线程：审批 TTL 是 30 分钟，检查点 TTL 是 72 小时，
    差着两个数量级。
    """
    hours = int(get_settings().agent_checkpoint_ttl_hours or 0)
    if hours <= 0:
        return 0
    saver = await acquire()
    if saver is None:
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    seen: set[str] = set()
    doomed: list[str] = []
    try:
        async for item in saver.alist(None, limit=SCAN_LIMIT):
            tid = ((item.config or {}).get("configurable") or {}).get("thread_id")
            if not tid or tid in seen:
                continue
            seen.add(tid)   # alist 按时间倒序，每个线程第一次出现就是最新一条
            when = _parse_ts((item.checkpoint or {}).get("ts"))
            if when and when < cutoff:
                doomed.append(tid)
    except Exception as exc:  # noqa: BLE001 — 清理失败不能挡住启动/定时任务
        print(f"[checkpoint] 扫描过期线程失败：{type(exc).__name__}: {exc}", flush=True)
        return 0

    removed = 0
    for tid in doomed:
        try:
            await saver.adelete_thread(tid)
            removed += 1
        except Exception:  # noqa: BLE001 — 单条失败跳过，下次再清
            continue
    if removed:
        print(f"[checkpoint] 清理过期线程 {removed} 个（>{hours}h 未更新）", flush=True)
    return removed


def describe() -> dict:
    """运维可见性：当前档位、落点、TTL、是否已连上。"""
    mode = resolve_mode()
    return {
        "mode": mode,
        "active": _MODE or ("memory" if mode == "memory" else ""),
        "target": _target(mode) if mode in ("sqlite", "postgres") else "",
        "ttl_hours": int(get_settings().agent_checkpoint_ttl_hours or 0),
        "connected": _SAVER is not None,
    }


__all__ = [
    "acquire",
    "close",
    "describe",
    "memory_saver",
    "purge_expired",
    "resolve_mode",
]
