"""路由层的「同步/异步」边界。

背景：全站数据库用同步 psycopg，文件读写与 subprocess 也是同步的。一个
`async def` 路由里跑这些，就等于在事件循环线程上排队——单个慢查询会把所有
请求（包括正在流式输出的对话）一起卡住。反过来，写成普通 `def`，FastAPI 会
自动把它调度到线程池，阻塞只影响那一个 worker 线程。

所以约定是：**路由函数体里没有 await 就别写 async**。这条用例把约定钉住，
免得下次又顺手补一个 `async def`。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROUTES = Path(__file__).resolve().parents[1] / "app" / "api" / "routes"


def _needlessly_async(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        body = list(ast.walk(node))
        if any(isinstance(n, (ast.Await, ast.AsyncFor, ast.AsyncWith)) for n in body):
            continue
        if any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in body):
            continue
        offenders.append(f"{path.name}::{node.name}")
    return offenders


@pytest.mark.parametrize("path", sorted(ROUTES.glob("*.py")), ids=lambda p: p.name)
def test_no_needlessly_async_route(path: Path) -> None:
    offenders = _needlessly_async(path)
    assert not offenders, (
        "这些路由没有 await 却声明为 async def，会占着事件循环跑同步 IO：\n  "
        + "\n  ".join(offenders)
        + "\n改成普通 def（FastAPI 会自动丢线程池）；确实要异步就补上真正的 await。"
    )
