"""摄取链路验收：格式解析 / 去重 / 版本 / 回滚 / 切片编辑 / 删除 / 异步任务 / 批量导入。

脚本会在自己的进程里起一个「假 worker」：从队列领任务、直接调 handler 执行，
所以**必须与被测服务共用同一份配置**（同一 .env、同一存储后端、同一队列）。
以前这里硬编码 sqlite，导致服务跑在 PG 上时，异步用例其实写进了 SQLite —— 表面
18/18，实际验的不是同一个库。现在一律继承 .env，需要换目标就换 --base 与 .env。

用法：
    ./.venv/Scripts/python.exe tools/verify_ingest.py                        # 打 8000
    ./.venv/Scripts/python.exe tools/verify_ingest.py --base http://127.0.0.1:8001
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  <- {detail}"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    opts = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    token = ""

    def call(method: str, path: str, body=None, *, raw: bytes | None = None,
             content_type: str = "application/json") -> tuple[int, object]:
        data = raw if raw is not None else (
            json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        )
        req = urllib.request.Request(base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", content_type)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with opts.open(req, timeout=120) as resp:
                payload = resp.read()
                return resp.status, json.loads(payload) if payload else None
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            try:
                return exc.code, json.loads(payload)
            except Exception:  # noqa: BLE001
                return exc.code, payload.decode("utf-8", "replace")

    def upload(path: str, filename: str, data: bytes) -> tuple[int, object]:
        boundary = "----verify" + uuid.uuid4().hex
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\";"
            f" filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
        return call("POST", path, raw=body,
                    content_type=f"multipart/form-data; boundary={boundary}")

    def upload_many(path: str, files: list[tuple[str, str, bytes]]) -> tuple[int, object]:
        """多文件 multipart：files = [(相对路径, 文件名, 内容)]，同时带 paths 字段。"""
        boundary = "----verify" + uuid.uuid4().hex
        body = b""
        for rel, _, _ in files:
            body += (
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"paths\"\r\n\r\n"
                f"{rel}\r\n"
            ).encode()
        for rel, filename, data in files:
            body += (
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\";"
                f" filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n"
            ).encode() + data + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        return call("POST", path, raw=body,
                    content_type=f"multipart/form-data; boundary={boundary}")

    # ---------------- 登录
    status, data = call("POST", "/api/auth/login",
                        {"username": "admin", "password": "admin123"})
    if status != 200:
        print(f"登录失败：{status} {data}")
        return 1
    token = data["token"]

    status, kb = call("POST", "/api/kb", {"name": f"摄取验收-{uuid.uuid4().hex[:6]}"})
    kb_id = kb["id"]
    print(f"\n验收知识库 {kb_id}")

    # 中途断言失败也要清库：否则每崩一次就留一个「摄取验收-xxxx」加一堆测试文档，
    # 在 PG 里看着像真实数据。
    import atexit

    atexit.register(lambda: call("DELETE", f"/api/kb/{kb_id}"))

    csv_v1 = "产品,价格\n甲,100\n乙,200\n".encode()
    csv_v2 = "产品,价格\n甲,120\n乙,200\n丙,300\n".encode()

    # ---------------- 同步上传 + 去重
    status, r1 = upload(f"/api/kb/{kb_id}/documents/upload", "报价.csv", csv_v1)
    check("CSV 同步上传成功", status == 200, str(r1))
    check("切片数 > 0", status == 200 and r1["chunk_count"] > 0, str(r1))

    status, r2 = upload(f"/api/kb/{kb_id}/documents/upload", "报价副本.csv", csv_v1)
    check("同内容再次上传被去重", status == 200 and r2.get("deduped") is True, str(r2))
    check("去重不新增文档", status == 200 and r2["id"] == r1["id"], str(r2))

    # ---------------- 同名新版本
    status, r3 = upload(f"/api/kb/{kb_id}/documents/upload", "报价.csv", csv_v2)
    check("同名不同内容记为 v2", status == 200 and r3.get("version") == 2, str(r3))

    status, versions = call("GET", f"/api/kb/{kb_id}/documents/{r1['id']}/versions")
    check("版本表有 2 条快照", status == 200 and len(versions) == 2, str(versions))

    # ---------------- 回滚
    status, r4 = call("POST", f"/api/kb/{kb_id}/documents/{r1['id']}/rollback",
                      {"version": 1})
    check("回滚到 v1 生成 v3", status == 200 and r4.get("version") == 3, str(r4))

    # ---------------- 切片预览与编辑
    status, chunks = call("GET", f"/api/kb/{kb_id}/documents/{r1['id']}/chunks")
    ok = status == 200 and len(chunks) > 0
    check("切片列表可读", ok, str(chunks)[:120])
    if ok:
        chunk = chunks[0]
        marker = f"编辑后标记{uuid.uuid4().hex[:6]}"
        status, edited = call("PATCH", f"/api/kb/{kb_id}/chunks/{chunk['id']}",
                              {"text": marker})
        check("切片编辑并重嵌入", status == 200 and marker in (edited or {}).get("text", ""),
              str(edited))

        status, hits = call("POST", "/api/kb/search",
                            {"kb_ids": [kb_id], "query": marker, "top_k": 3})
        hit_texts = [h["text"] for h in hits] if isinstance(hits, list) else []
        check("编辑后的文本能检索命中", status == 200 and any(marker in t for t in hit_texts),
              str(hits)[:160])

    # ---------------- S2.3：父子切片 / 引用定位 / 多策略对比
    pc_text = (
        "## 检索验收章节\n本段用于父子切片验收。" + "关键信息：检索验收标记%d。" % 42 + "\n"
        "补充背景" * 200
    )
    status, pc_doc = call("POST", f"/api/kb/{kb_id}/documents",
                          {"name": "父子切片.md", "content": pc_text, "strategy": "parent-child"})
    check("父子切片入库", status == 200 and pc_doc.get("chunk_count", 0) > 0, str(pc_doc)[:160])
    status, pc_chunks = call("GET", f"/api/kb/{kb_id}/documents/{pc_doc['id']}/chunks")
    pc_all = status == 200 and pc_chunks and all(c.get("parent_seq") is not None for c in pc_chunks)
    check("孩子片都带 parent_seq", pc_all, str(pc_chunks)[:160])

    status, hits = call("POST", "/api/kb/search",
                        {"kb_ids": [kb_id], "query": "检索验收标记", "top_k": 2,
                         "parent_expand": True})
    expanded_ok = status == 200 and hits and any("关键信息" in h["text"] and len(h["text"]) > 300
                                                 for h in hits)
    check("父片回填（命中孩子片返回父片全文）", expanded_ok, str(hits)[:200])

    status, cite = call("GET", f"/api/kb/citations/{hits[0]['chunk_id']}") if status == 200 and hits \
        else (404, None)
    check("引用定位返回原文偏移", status == 200 and cite and cite.get("char_end", 0) > cite.get("char_start", 0)
          and "excerpt" in cite, str(cite)[:200])

    status, cmp_res = call("POST", "/api/kb/search_compare",
                           {"kb_ids": [kb_id], "query": "检索验收标记", "top_k": 2,
                            "modes": ["hybrid", "lexical"]})
    modes_ok = status == 200 and set(cmp_res.get("results", {})) == {"hybrid", "lexical"}
    check("多策略并排对比", modes_ok, str(cmp_res)[:200])

    # ---------------- 异步上传
    status, job = upload(f"/api/kb/{kb_id}/documents/async", "异步.csv", csv_v2)
    check("异步上传立即返回 job_id", status == 200 and job.get("job_id"), str(job))
    job_id = job.get("job_id", "")

    if job_id:
        from app.services.queue import get_queue
        from app.workers.worker import execute

        q = get_queue()
        claimed = q.claim()
        check("worker 领到任务", claimed is not None and claimed.id == job_id,
              f"claimed={claimed.id if claimed else None}")
        if claimed:
            execute(q, claimed)
        done = q.get(job_id)
        check("异步任务完成", done is not None and done.status == "done",
              f"{done.status if done else 'NA'} {done.error if done else ''}")
        check("异步任务留下进度", done is not None and done.progress.get("percent") == 100,
              str(done.progress if done else {}))

    # ---------------- 永久失败不重试
    status, bad = upload(f"/api/kb/{kb_id}/documents/async", "坏格式.xyz", b"not a doc")
    if status == 200 and bad.get("job_id"):
        from app.services.queue import get_queue
        from app.workers.worker import execute

        q = get_queue()
        claimed = q.claim()
        if claimed:
            execute(q, claimed)
        failed = q.get(bad["job_id"])
        check("不支持的格式直接 failed", failed is not None and failed.status == "failed",
              f"{failed.status if failed else 'NA'}")

    # ---------------- 在途任务 + 删库：不能留下孤儿文档
    # 任务入队后库被删，worker 才消费——曾经会往已删除的库里写文档/切片/向量，
    # 列表里看不见却白占存储，只能手工清。这里守住这个边界。
    status, kb2 = call("POST", "/api/kb", {"name": f"竞态验收-{uuid.uuid4().hex[:6]}"})
    if status == 200 and kb2.get("id"):
        atexit.register(lambda: call("DELETE", f"/api/kb/{kb2['id']}"))
        status, j2 = upload(f"/api/kb/{kb2['id']}/documents/async", "在途.md",
                            "# 在途\n这段文本对应一个马上被删除的知识库。\n".encode())
        ds, dbody = call("DELETE", f"/api/kb/{kb2['id']}")
        check("删库成功（任务仍在队列里）", ds == 200, f"{ds} {dbody}")
        if status == 200 and j2.get("job_id"):
            from app.services.queue import get_queue
            from app.services.store import get_store
            from app.workers.worker import execute

            q = get_queue()
            claimed = q.claim()
            if claimed:
                execute(q, claimed)
            refused = q.get(j2["job_id"])
            check("删库后在途任务被拒（不重试）",
                  refused is not None and refused.status == "failed"
                  and "已被删除" in (refused.error or ""),
                  f"{refused.status if refused else 'NA'} {refused.error if refused else ''}")
            left = [d for d in get_store().repo.list_documents_all() if d.kb_id == kb2["id"]]
            check("删库后不留孤儿文档", not left, f"残留 {len(left)} 篇")

    # ---------------- 删除文档
    status, deleted = call("DELETE", f"/api/kb/{kb_id}/documents/{r1['id']}")
    check("删除文档成功", status == 200 and deleted.get("ok") is True, str(deleted))
    status, chunks_after = call("GET", f"/api/kb/{kb_id}/documents/{r1['id']}/chunks")
    check("删除后切片为空（或 404）", status == 404 or chunks_after == [], str(chunks_after))

    # ---------------- URL 导入（离线站点，失败也应给出明确 4xx）
    status, url_res = call("POST", f"/api/kb/{kb_id}/documents/url",
                           {"url": "http://127.0.0.1:9/nope"})
    check("URL 抓取失败返回 400 而非 500", status == 400, f"{status} {url_res}")

    # ---------------- 批量导入：目录（多文件 + 相对路径 + 一个坏文件）
    from app.services.queue import get_queue as _get_queue
    from app.services.store import get_store as _get_store
    from app.workers.worker import execute as _execute

    def drain(job_id: str):
        q = _get_queue()
        claimed = q.claim()
        if claimed:
            _execute(q, claimed)
        return q.get(job_id)

    status, batch = upload_many(f"/api/kb/{kb_id}/documents/batch", [
        ("产品/需求.md", "需求.md", "# 需求\n批量导入要能一次吃下一个目录。\n".encode()),
        ("产品/接口/规范.md", "规范.md", "# 规范\nGET /api/kb 返回知识库列表。\n".encode()),
        ("会议/周会.txt", "周会.txt", "周会纪要\n讨论了目录导入的排期。\n".encode()),
        ("产品/坏文件.pdf", "坏文件.pdf", b"this is not a pdf"),
    ])
    check("批量导入返回 job_id", status == 200 and batch.get("job_id"), str(batch)[:200])
    check("批量导入条目数正确", status == 200 and batch.get("files") == 4, str(batch)[:200])
    if status == 200 and batch.get("job_id"):
        done = drain(batch["job_id"])
        result = (done.result or {}) if done else {}
        check("批量任务完成", done is not None and done.status == "done",
              f"{done.status if done else 'NA'} {done.error if done else ''}")
        check("坏文件只失败一条、不中断整批",
              result.get("ok") == 3 and result.get("failed") == 1, str(result)[:240])
        names = [d.name for d in _get_store().repo.list_documents_all() if d.kb_id == kb_id]
        check("文档名保留目录结构", "产品/接口/规范.md" in names, str(names))

    # ---------------- 批量导入：ZIP（内部目录 + 垃圾条目）
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("资料/制度/报销.md", "# 报销\n差旅报销 10 个工作日内提交。\n")
        zf.writestr("__MACOSX/._报销.md", b"junk")
        zf.writestr("资料/.DS_Store", b"junk")
    status, zipped = upload_many(f"/api/kb/{kb_id}/documents/batch",
                                 [("资料.zip", "资料.zip", buf.getvalue())])
    check("ZIP 上传返回 job_id", status == 200 and zipped.get("job_id"), str(zipped)[:200])
    skipped_names = [s.get("name") for s in (zipped.get("skipped") or [])] if status == 200 else []
    check("ZIP 内垃圾条目被跳过", any("__MACOSX" in n for n in skipped_names), str(skipped_names))
    if status == 200 and zipped.get("job_id"):
        done = drain(zipped["job_id"])
        result = (done.result or {}) if done else {}
        check("ZIP 解包后全部入库", done is not None and done.status == "done" and result.get("ok") == 1,
              str(result)[:200])
        names = [d.name for d in _get_store().repo.list_documents_all() if d.kb_id == kb_id]
        check("ZIP 内目录结构保留", "资料/制度/报销.md" in names, str(names))

    # ---------------- 服务端目录导入：默认必须关闭
    from app.core.config import get_settings as _get_settings

    if not _get_settings().import_roots:
        status, denied = call("POST", f"/api/kb/{kb_id}/documents/dir", {"path": "C:/Windows"})
        check("目录导入默认关闭（403 且说明怎么开）",
              status == 403 and "APP_LOCAL_IMPORT_ROOTS" in str(denied), f"{status} {denied}")

    # ---------------- 清理
    status, _ = call("DELETE", f"/api/kb/{kb_id}")
    if status != 200:
        print(f"  ! 清理失败：DELETE /api/kb/{kb_id} -> {status}")

    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r[1])
    print(f"\n===== {passed} / {total} 通过")
    if passed != total:
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
