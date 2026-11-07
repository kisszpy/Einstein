"""基础设施连通性自检。

用法：
    ./.venv/Scripts/python.exe tools/check_infra.py
    ./.venv/Scripts/python.exe tools/check_infra.py --require-all   # 任一不通则退出码 1

设计意图：把「后端到底是真连上了容器，还是在用本地降级实现」这件事显式化。
v0.1 的教训是配置项写了但没人读，所以这里同时校验「配置声明」与「实际可达」。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402

OK = "OK"
FAIL = "FAIL"
SKIP = "SKIP"


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str = ""
    latency_ms: float | None = None
    hint: str = ""


@dataclass
class Report:
    results: list[CheckResult] = field(default_factory=list)

    def add(self, r: CheckResult) -> None:
        self.results.append(r)

    @property
    def failed(self) -> list[CheckResult]:
        return [r for r in self.results if r.status == FAIL]

    def render(self) -> str:
        width = max(len(r.name) for r in self.results) + 2
        lines = []
        for r in self.results:
            lat = f"{r.latency_ms:7.1f}ms" if r.latency_ms is not None else "        "
            lines.append(f"{r.status:4s} {r.name:<{width}} {lat}  {r.detail}")
            if r.hint:
                lines.append(f"     {'':<{width}}        └─ {r.hint}")
        return "\n".join(lines)


def _mask_dsn(dsn: str) -> str:
    """隐去口令，只保留 用户@主机:端口/库。"""
    if "://" not in dsn:
        return dsn
    scheme, rest = dsn.split("://", 1)
    if "@" in rest:
        cred, host = rest.split("@", 1)
        user = cred.split(":", 1)[0]
        return f"{scheme}://{user}:***@{host}"
    return dsn


def check_postgres(dsn: str) -> CheckResult:
    try:
        import psycopg
    except ImportError:
        return CheckResult(
            "postgres", SKIP, "psycopg 未安装",
            hint="pip install 'psycopg[binary]'",
        )
    t0 = time.perf_counter()
    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("select version(), current_database()")
                version, db = cur.fetchone()
        ms = (time.perf_counter() - t0) * 1000
        short = version.split(" on ")[0]
        return CheckResult("postgres", OK, f"{db} · {short}", ms)
    except Exception as exc:  # noqa: BLE001 — 自检要把所有错误都变成可读结论
        ms = (time.perf_counter() - t0) * 1000
        return CheckResult(
            "postgres", FAIL, f"{_mask_dsn(dsn)} → {type(exc).__name__}: {str(exc)[:90]}", ms,
            hint="确认容器已起：docker compose -f deploy/docker-compose.yml up -d postgres",
        )


def check_redis(url: str) -> CheckResult:
    try:
        import redis
    except ImportError:
        return CheckResult(
            "redis", SKIP, "redis 客户端未安装", hint="pip install redis",
        )
    t0 = time.perf_counter()
    try:
        client = redis.Redis.from_url(url, socket_connect_timeout=5, socket_timeout=5)
        client.ping()
        info = client.info("server")
        client.set("einstein:infra:probe", "1", ex=30)
        val = client.get("einstein:infra:probe")
        ms = (time.perf_counter() - t0) * 1000
        if val != b"1":
            return CheckResult("redis", FAIL, "PING 通但读写校验失败", ms)
        return CheckResult("redis", OK, f"v{info.get('redis_version', '?')} · 读写正常", ms)
    except Exception as exc:  # noqa: BLE001
        ms = (time.perf_counter() - t0) * 1000
        return CheckResult(
            "redis", FAIL, f"{type(exc).__name__}: {str(exc)[:90]}", ms,
            hint="docker compose -f deploy/docker-compose.yml up -d redis",
        )


def check_milvus(uri: str, collection: str) -> CheckResult:
    try:
        from pymilvus import MilvusClient
    except ImportError:
        return CheckResult(
            "milvus", SKIP, "pymilvus 未安装", hint="pip install pymilvus（注意 milvus-lite 不支持 Windows）",
        )
    t0 = time.perf_counter()
    try:
        client = MilvusClient(uri=uri)
        collections = client.list_collections()
        detail = f"{uri} · {len(collections)} 个集合"
        if collection and client.has_collection(collection):
            stats = client.get_collection_stats(collection)
            detail += f" · {collection}={stats.get('row_count', '?')} 条"
        client.close()
        ms = (time.perf_counter() - t0) * 1000
        return CheckResult("milvus", OK, detail, ms)
    except Exception as exc:  # noqa: BLE001
        ms = (time.perf_counter() - t0) * 1000
        return CheckResult(
            "milvus", FAIL, f"{uri} → {type(exc).__name__}: {str(exc)[:90]}", ms,
            hint="Milvus 首次启动约需 90s；查看 docker logs einstein-milvus",
        )


def check_llm(base_url: str, model: str, api_key: str = "") -> CheckResult:
    """模型端点：只探 /models，不触发推理（避免拖慢自检）。"""
    import json
    import urllib.error
    import urllib.request

    url = base_url.rstrip("/") + "/models"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url)
        if api_key and api_key != "not-needed":
            req.add_header("Authorization", f"Bearer {api_key}")
        with opener.open(req, timeout=8) as resp:
            payload = json.loads(resp.read() or b"{}")
        ms = (time.perf_counter() - t0) * 1000
        names = [m.get("id", "") for m in payload.get("data", [])][:3]
        listed = ", ".join(names) or model
        warn = "" if model in names or not names else f"（配置的 {model} 不在列表中）"
        return CheckResult("llm-endpoint", OK, f"{url} · {listed}{warn}", ms)
    except urllib.error.HTTPError as exc:
        ms = (time.perf_counter() - t0) * 1000
        reason = "密钥无效" if exc.code in (401, 403) else f"HTTP {exc.code}"
        return CheckResult(
            "llm-endpoint", FAIL, f"{url} → {reason}", ms,
            hint="检查 APP_LLM_API_KEY / APP_LLM_BASE_URL；云端模型需可访问外网",
        )
    except urllib.error.URLError as exc:
        ms = (time.perf_counter() - t0) * 1000
        return CheckResult(
            "llm-endpoint", FAIL, f"{url} → {exc.reason}", ms,
            hint="模型端点不可达时问答不可用；只验基础设施可加 --skip-llm",
        )
    except Exception as exc:  # noqa: BLE001
        ms = (time.perf_counter() - t0) * 1000
        return CheckResult("llm-endpoint", FAIL, f"{type(exc).__name__}: {str(exc)[:90]}", ms)


def check_embedding(base_url: str, model: str) -> CheckResult:
    """嵌入端点：真的做一次极小向量化，验证维度可用。"""
    import json
    import urllib.request

    url = base_url.rstrip("/") + "/embeddings"
    body = json.dumps({"model": model, "input": ["自检"]}).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    t0 = time.perf_counter()
    try:
        with opener.open(req, timeout=30) as resp:
            payload = json.loads(resp.read())
        ms = (time.perf_counter() - t0) * 1000
        dim = len(payload["data"][0]["embedding"])
        return CheckResult("embedding-endpoint", OK, f"维度 {dim}", ms)
    except Exception as exc:  # noqa: BLE001
        ms = (time.perf_counter() - t0) * 1000
        return CheckResult(
            "embedding-endpoint", FAIL, f"{type(exc).__name__}: {str(exc)[:90]}", ms,
            hint="嵌入不可用时只能走 lexical 检索，上传文档会失败",
        )


def check_backends_declared() -> list[CheckResult]:
    """配置声明层：确认三个开关的值是合法枚举，避免拼错后静默降级。"""
    s = get_settings()
    rows = [
        ("config.store_backend", s.store_backend, {"json", "sqlite", "postgres"}),
        ("config.vector_backend", s.vector_backend, {"memory", "milvus"}),
        ("config.queue_backend", s.queue_backend, {"local", "redis"}),
    ]
    out = []
    for name, value, allowed in rows:
        if value in allowed:
            out.append(CheckResult(name, OK, value))
        else:
            out.append(
                CheckResult(
                    name, FAIL, f"{value!r} 不在 {sorted(allowed)} 中",
                    hint="拼写错误会导致启动时回退到默认后端，请在 .env 中修正",
                )
            )
    return out


def check_data_dir() -> CheckResult:
    s = get_settings()
    path = Path(s.sqlite_path).parent
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return CheckResult("data-dir", OK, str(path.resolve()))
    except Exception as exc:  # noqa: BLE001
        return CheckResult("data-dir", FAIL, f"{path} → {exc}")


def deep_postgres(dsn: str) -> CheckResult:
    """真实读写：建表 → 插入 → 查询 → 回滚式清理。"""
    try:
        import psycopg
    except ImportError:
        return CheckResult("deep.postgres", SKIP, "psycopg 未安装")

    t0 = time.perf_counter()
    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "CREATE TABLE IF NOT EXISTS _infra_probe ("
                    " id serial PRIMARY KEY, note text, ts timestamptz DEFAULT now())"
                )
                cur.execute("INSERT INTO _infra_probe (note) VALUES (%s) RETURNING id", ("probe",))
                row_id = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM _infra_probe WHERE note = %s", ("probe",))
                count = cur.fetchone()[0]
                cur.execute("DELETE FROM _infra_probe WHERE id = %s", (row_id,))
                cur.execute("DROP TABLE IF EXISTS _infra_probe")
            conn.commit()
        ms = (time.perf_counter() - t0) * 1000
        if count < 1:
            return CheckResult("deep.postgres", FAIL, "插入后查不到数据（事务异常）", ms)
        return CheckResult("deep.postgres", OK, f"建表/插入/查询/清理通过（命中 {count} 行）", ms)
    except Exception as exc:  # noqa: BLE001
        ms = (time.perf_counter() - t0) * 1000
        return CheckResult("deep.postgres", FAIL, f"{type(exc).__name__}: {str(exc)[:100]}", ms)


def deep_redis(url: str) -> CheckResult:
    """队列语义：LPUSH + BRPOP，以及带过期的键。"""
    try:
        import redis
    except ImportError:
        return CheckResult("deep.redis", SKIP, "redis 客户端未安装")

    t0 = time.perf_counter()
    try:
        client = redis.Redis.from_url(url, socket_connect_timeout=5, socket_timeout=5)
        key = "einstein:infra:queue"
        client.delete(key)
        client.lpush(key, "job-1", "job-2")
        popped = client.brpop(key, timeout=3)
        client.set("einstein:infra:ttl", "1", ex=5)
        ttl = client.ttl("einstein:infra:ttl")
        client.delete(key, "einstein:infra:ttl")
        ms = (time.perf_counter() - t0) * 1000
        if not popped:
            return CheckResult("deep.redis", FAIL, "BRPOP 未取到元素（队列语义异常）", ms)
        return CheckResult(
            "deep.redis", OK, f"队列出队 {popped[1].decode()} · TTL={ttl}s", ms,
        )
    except Exception as exc:  # noqa: BLE001
        ms = (time.perf_counter() - t0) * 1000
        return CheckResult("deep.redis", FAIL, f"{type(exc).__name__}: {str(exc)[:100]}", ms)


def deep_milvus(uri: str) -> CheckResult:
    """真实检索：建集合 → 插入 → search 命中 → 删集合。"""
    try:
        from pymilvus import MilvusClient
    except ImportError:
        return CheckResult("deep.milvus", SKIP, "pymilvus 未安装")

    name = "_infra_probe"
    t0 = time.perf_counter()
    client = None
    try:
        client = MilvusClient(uri=uri)
        if client.has_collection(name):
            client.drop_collection(name)
        client.create_collection(collection_name=name, dimension=8, metric_type="COSINE", auto_id=True)
        client.insert(
            collection_name=name,
            data=[
                {"vector": [1.0, 0, 0, 0, 0, 0, 0, 0], "note": "axis-x"},
                {"vector": [0, 1.0, 0, 0, 0, 0, 0, 0], "note": "axis-y"},
                {"vector": [0, 0, 1.0, 0, 0, 0, 0, 0], "note": "axis-z"},
            ],
        )
        client.flush(name)
        hits = client.search(
            collection_name=name, data=[[0.9, 0.1, 0, 0, 0, 0, 0, 0]], limit=2, output_fields=["note"],
        )
        top = hits[0][0] if hits and hits[0] else None
        client.drop_collection(name)
        client.close()
        ms = (time.perf_counter() - t0) * 1000
        if not top:
            return CheckResult("deep.milvus", FAIL, "检索无命中（索引未生效）", ms)
        return CheckResult(
            "deep.milvus", OK,
            f"建/插/检索/删通过 · top1={top.get('entity', {}).get('note')} score={top.get('distance'):.3f}", ms,
        )
    except Exception as exc:  # noqa: BLE001
        ms = (time.perf_counter() - t0) * 1000
        if client is not None:
            try:
                client.drop_collection(name)
            except Exception:  # noqa: BLE001 — 清理失败不掩盖主错误
                pass
        return CheckResult("deep.milvus", FAIL, f"{type(exc).__name__}: {str(exc)[:110]}", ms)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-all", action="store_true", help="任一 FAIL 返回非零退出码")
    parser.add_argument("--skip-llm", action="store_true", help="跳过模型端点（只验基础设施）")
    parser.add_argument("--deep", action="store_true", help="真实读写冒烟（建表/入队/向量检索）")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    s = get_settings()
    report = Report()
    for r in check_backends_declared():
        report.add(r)
    report.add(check_data_dir())
    report.add(check_postgres(s.postgres_dsn))
    report.add(check_redis(s.redis_url))
    report.add(check_milvus(s.milvus_uri, s.milvus_collection))
    if not args.skip_llm:
        report.add(check_llm(s.llm_base_url, s.llm_model, s.llm_api_key))
        report.add(check_embedding(s.embedding_base_url, s.embedding_model))
    if args.deep:
        report.add(deep_postgres(s.postgres_dsn))
        report.add(deep_redis(s.redis_url))
        report.add(deep_milvus(s.milvus_uri))

    print("Einstein 基础设施自检")
    print(f"  存储后端 = {s.store_backend} | 向量后端 = {s.vector_backend} | 队列后端 = {s.queue_backend}")
    print("-" * 78)
    print(report.render())
    print("-" * 78)

    if report.failed:
        print(f"结论：{len(report.failed)} 项不可用 — {', '.join(r.name for r in report.failed)}")
        print("说明：不可用不等于不可开发，store/vector/queue 均有本地降级实现。")
        if args.verbose:
            traceback.print_exc()
    else:
        print("结论：全部可用")
    return 1 if (args.require_all and report.failed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
