"""SDK 冒烟：对着一个运行中的实例跑通最常用的几条路径。

    python sdk/smoke_client.py                                  # 默认 http://127.0.0.1:8000
    python sdk/smoke_client.py --base http://127.0.0.1:8001

覆盖：登录 → 建库 → 粘贴入库 → 检索 → 上传（异步 + 轮询）→ 改切片 → 对话 → 清理。
异步上传需要 worker 在跑，否则这一项会明确失败而不是静默通过。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from einstein_client import EinsteinClient, EinsteinError  # noqa: E402

PASS = 0
FAIL = 0


def ok(label: str, extra: str = "") -> None:
    global PASS
    PASS += 1
    print(f"  ✓ {label}{('：' + extra) if extra else ''}")


def bad(label: str, extra: str = "") -> None:
    global FAIL
    FAIL += 1
    print(f"  ✗ {label}{('：' + extra) if extra else ''}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default="admin123")
    parser.add_argument("--keep", action="store_true", help="保留测试知识库，便于手工查看")
    args = parser.parse_args()

    print(f"目标 {args.base}")
    client = EinsteinClient(args.base)

    # ---- 认证 ----
    try:
        client.login(args.user, args.password)
        ok("登录", args.user)
    except EinsteinError as exc:
        bad("登录失败", str(exc)[:160])
        return 1

    try:
        me = client.me()
        ok("me", (me.get("user") or {}).get("username", "?"))
    except EinsteinError as exc:
        bad("me 失败", str(exc)[:160])

    # ---- 建库 ----
    name = f"sdk-smoke-{int(time.time())}"
    kb = {}
    try:
        kb = client.create_kb(name)
        ok("建库", name)
    except EinsteinError as exc:
        bad("建库失败", str(exc)[:160])
        return 1
    kb_id = kb["id"]

    try:
        # ---- 粘贴入库 ----
        doc = client.add_text(kb_id, "报销.txt", (
            "差旅报销流程\n"
            "出差结束后 10 个工作日内提交报销单。\n"
            "审批链路：直属主管 → 部门负责人 → 财务复核。\n"
        ))
        if doc.get("chunk_count", 0) > 0:
            ok("粘贴入库", f"{doc['chunk_count']} 段 / v{doc.get('version')}")
        else:
            bad("粘贴入库", "切片数为 0")

        # ---- 检索 ----
        hits = client.search([kb_id], "报销流程", top_k=3)
        if hits:
            ok("检索", f"命中 {len(hits)} 条，最高分 {hits[0].get('score', 0):.3f}")
        else:
            bad("检索", "无命中")

        # ---- 切片编辑 ----
        chunks = client.document_chunks(kb_id, doc["id"])
        if chunks:
            patch = client.patch_chunk(kb_id, chunks[0]["id"], chunks[0]["text"] + "\n补充：逾期需补充说明。")
            if patch.get("chars", 0) > 0:
                ok("切片编辑", f"{patch['chars']} 字")
            else:
                bad("切片编辑", "返回异常")
        else:
            bad("切片编辑", "拿不到切片")

        # ---- 异步上传 ----
        tmp = Path(__file__).resolve().parent / "_smoke_upload.md"
        tmp.write_text("# 请假制度\n年假按入职年限计算。\n三天以上需提前一周申请。\n", encoding="utf-8")
        try:
            job = client.upload_document(kb_id, tmp, wait=True, timeout=180)
            if job.status == "done":
                ok("异步上传", f"job={job.job_id} 切片 {job.result.get('chunk_count', '?')} 段")
            else:
                bad("异步上传", f"状态 {job.status} {job.error[:120]}")
        except EinsteinError as exc:
            bad("异步上传", f"{str(exc)[:140]}（检查 worker 是否在跑）")
        finally:
            tmp.unlink(missing_ok=True)

        # ---- 对话 ----
        try:
            answer = client.chat("差旅报销的审批链路是什么？", kb_ids=[kb_id])
            if answer.strip():
                ok("对话", f"{len(answer)} 字：{answer.strip()[:40]}…")
            else:
                bad("对话", "回答为空")
        except EinsteinError as exc:
            bad("对话失败", str(exc)[:160])

        # ---- 任务与审计 ----
        try:
            jobs = client.list_jobs(limit=5)
            ok("任务列表", f"{len(jobs)} 条")
        except EinsteinError as exc:
            bad("任务列表", str(exc)[:160])
    finally:
        if not args.keep:
            try:
                client._request("DELETE", f"/api/kb/{kb_id}")  # noqa: SLF001 — 冒烟脚本，直接用底层请求
                print(f"  · 已删除测试库 {name}")
            except EinsteinError:
                print(f"  · 测试库 {name} 删除失败，可手工清理")

    print(f"\n通过 {PASS} · 失败 {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
