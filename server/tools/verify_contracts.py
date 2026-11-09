"""协议契约检查：每个 Protocol 的成员，所有实现类都必须有。

为什么需要它：Protocol 不做运行时校验，漏实现一个方法只有在**某条代码路径真的走到**
时才炸——JSON 后端的版本表就漏过 `add_version`，因为验收脚本跑在 SQLite 实例上，
这条路径一直没被踩到。这个脚本把这类问题提前到 CI。

用法：
    python tools/verify_contracts.py          # 有缺口则退出码 1
    python tools/verify_contracts.py -v       # 同时打印每个协议覆盖了多少成员
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Protocol

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.queue.base import JobQueue  # noqa: E402
from app.services.queue.local import LocalQueue  # noqa: E402
from app.services.queue.redis_queue import RedisQueue  # noqa: E402
from app.services.repositories.history import (  # noqa: E402
    HistoryRepository,
    JsonHistoryRepository,
    SqlHistoryRepository,
)
from app.services.repositories.kb import (  # noqa: E402
    JsonKbRepository,
    KbRepository,
    SqlKbRepository,
)
from app.services.state import JsonState, StateBackend, TableState  # noqa: E402
from app.services.vector.base import VectorIndex  # noqa: E402
from app.services.vector.memory import MemoryIndex  # noqa: E402
from app.services.vector.milvus import MilvusIndex  # noqa: E402

# 协议 → 必须完整实现它的类
CONTRACTS: list[tuple[type, list[type]]] = [
    (KbRepository, [JsonKbRepository, SqlKbRepository]),
    (HistoryRepository, [JsonHistoryRepository, SqlHistoryRepository]),
    (JobQueue, [LocalQueue, RedisQueue]),
    (VectorIndex, [MemoryIndex, MilvusIndex]),
    # 两种后端：JSON 文件（降级路径）与按域拆表的 TableState（sqlite/postgres）
    (StateBackend, [JsonState, TableState]),
]


def protocol_members(proto: type) -> list[str]:
    """协议声明的成员名。只取协议自身 __dict__，不继承 Any。"""
    names = []
    for name, value in vars(proto).items():
        if name.startswith("_"):
            continue
        if callable(value) or isinstance(value, property):
            names.append(name)
    return sorted(names)


def implements(cls: type, name: str) -> bool:
    """动态拼接的类（如 __getattr__）也算有；这里只认真实属性。"""
    return hasattr(cls, name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    failures = 0
    checked = 0
    for proto, impls in CONTRACTS:
        members = protocol_members(proto)
        if not isinstance(proto, type) or not issubclass(proto, Protocol) and not proto.__dict__.get("_is_protocol"):
            print(f"[warn] {proto.__name__} 不是 Protocol，跳过")
            continue
        if not members:
            print(f"[warn] {proto.__name__} 没有可检查的成员")
            continue
        checked += len(members) * len(impls)
        if args.verbose:
            print(f"{proto.__name__}: {len(members)} 个成员 → {len(impls)} 个实现")
        for cls in impls:
            missing = [m for m in members if not implements(cls, m)]
            if missing:
                failures += 1
                print(f"[FAIL] {cls.__name__} 未实现 {proto.__name__} 的：{', '.join(missing)}")
            elif args.verbose:
                print(f"  ok {cls.__name__}")

    if failures:
        print(f"\n契约检查未通过：{failures} 个类有缺口")
        return 1
    print(f"[contracts] {len(CONTRACTS)} 个协议、{checked} 项成员全部对齐")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
