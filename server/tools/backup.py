"""备份与恢复（含保留策略与恢复演练）。

覆盖三种存储后端：
- json    → 打包 server/data 下的状态文件
- sqlite  → 用 sqlite3 backup API 复制（在线安全，不锁库）
- postgres→ pg_dump（优先走容器，可用 --pg-container 指定）

用法：
    python tools/backup.py backup                       # 按当前 APP_STORE_BACKEND
    python tools/backup.py backup --prune 14            # 备份后只留最近 14 份
    python tools/backup.py list
    python tools/backup.py verify backups/einstein-xxx.tar.gz
    python tools/backup.py prune --keep 14
    python tools/backup.py restore backups/einstein-20261107-130000.tar.gz --yes

**为什么要有 verify**：备份脚本最大的失败模式不是报错，而是"每天成功地产出一个
空包"。等到真要恢复才发现备份里没有表，损失的是整整一个保留周期。verify 打开
归档、按 manifest 逐项核对、对 sqlite 跑 integrity_check、对 json 逐份解析、
对 pg_dump 检查是否真含 COPY/CREATE——不通过就以非 0 退出，能直接挂进 cron/CI。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SERVER_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = SERVER_ROOT / "data"
BACKUP_DIR = SERVER_ROOT / "backups"

# 配置域 + 知识库 + 会话，以及运行期产物
STATE_FILES = [
    "kb_store.json", "history_store.json", "experts_store.json", "skills_store.json",
    "users.json", "workspaces.json", "api_keys.json", "audit.json",
    "eval_sets.json", "mcp_servers.json", "sessions.json", "metrics.json",
]


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _pg_dsn() -> str:
    from app.core.config import get_settings

    return get_settings().postgres_dsn


def _dump_postgres(target: Path, container: str) -> str:
    cmd = [
        "docker", "exec", container, "pg_dump", "-U", "einstein", "-d", "einstein",
        "--no-owner", "--clean", "--if-exists",
    ]
    try:
        with target.open("wb") as fh:
            proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.PIPE, check=False)
    except FileNotFoundError as exc:
        raise SystemExit("找不到 docker 命令；请改用 --backend sqlite / json，或手动 pg_dump") from exc
    if proc.returncode != 0:
        raise SystemExit(f"pg_dump 失败：{proc.stderr.decode('utf-8', 'replace')[:500]}")
    return f"docker exec {container} pg_dump"


def _restore_postgres(source: Path, container: str) -> None:
    cmd = [
        "docker", "exec", "-i", container, "psql", "-U", "einstein", "-d", "einstein",
    ]
    with source.open("rb") as fh:
        proc = subprocess.run(cmd, stdin=fh, stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0:
        raise SystemExit(f"psql 恢复失败：{proc.stderr.decode('utf-8', 'replace')[:500]}")


def backup(backend: str, pg_container: str) -> Path:
    from app.core.config import get_settings

    settings = get_settings()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    out = BACKUP_DIR / f"einstein-{_stamp()}-{backend}.tar.gz"
    manifest: dict = {"backend": backend, "created_at": datetime.now().isoformat(), "files": []}

    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "stage"
        stage.mkdir()

        if backend == "json":
            for name in STATE_FILES:
                src = DATA_DIR / name
                if src.exists():
                    shutil.copy2(src, stage / name)
                    manifest["files"].append(name)
        elif backend == "sqlite":
            src = Path(settings.sqlite_path)
            if not src.is_absolute():
                src = SERVER_ROOT / src
            if not src.exists():
                raise SystemExit(f"sqlite 文件不存在：{src}")
            dst = stage / "einstein.db"
            # 注意：sqlite3 的连接上下文管理器只管事务、不关连接，
            # Windows 上未关闭的句柄会让临时目录删不掉。
            source_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
            target_conn = sqlite3.connect(dst)
            try:
                source_conn.backup(target_conn)
            finally:
                target_conn.close()
                source_conn.close()
            manifest["files"].append("einstein.db")
        elif backend == "postgres":
            dump = stage / "postgres.sql"
            manifest["command"] = _dump_postgres(dump, pg_container)
            manifest["files"].append("postgres.sql")
        else:
            raise SystemExit(f"未知后端：{backend}")

        # 上传的原始文件（异步摄取的任务源文件，失败重试还要用）
        uploads = DATA_DIR / "uploads"
        if uploads.exists() and any(uploads.iterdir()):
            shutil.copytree(uploads, stage / "uploads")
            manifest["files"].append("uploads/")

        (stage / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        with tarfile.open(out, "w:gz") as tar:
            tar.add(stage, arcname="einstein")

    size = out.stat().st_size / 1024
    print(f"已备份：{out}（{size:.0f} KB，{len(manifest['files'])} 项）")
    return out


def _backup_files() -> list[Path]:
    """按修改时间倒序的备份文件（文件名里的时间戳只精确到秒，同秒会乱序）。"""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(
        BACKUP_DIR.glob("einstein-*.tar.gz"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def prune(keep: int = 14, days: int = 0) -> tuple[int, int]:
    """删掉超出保留策略的备份，返回 (删除份数, 释放字节)。

    两条规则同时生效：`keep` 管"至少留几份"，`days` 管"最老留到几天前"。
    只写 keep 的常见坑是——连续一周备份失败，最后 14 份全是上个月的老包，
    看起来"还有 14 份"，实际最新的已经是 30 天前。
    """
    files = _backup_files()
    drop: set[Path] = set()
    if keep > 0:
        drop.update(files[keep:])
    if days > 0:
        cutoff = datetime.now().timestamp() - days * 86400
        drop.update(p for p in files if p.stat().st_mtime < cutoff)
    freed = sum(p.stat().st_size for p in drop if p.exists())
    for path in sorted(drop):
        path.unlink(missing_ok=True)
    return len(drop), freed


def verify(archive: Path) -> list[tuple[bool, str]]:
    """恢复演练的轻量版：只读检查归档能不能真的恢复出东西。

    不做"恢复到临时库再比对"——那要一套独立的数据库实例，脚本会在大多数
    环境里因为"没有临时库"而失败，最后没人跑它。这里做的是一组能自动判定
    的静态检查，覆盖"空包""缺文件""库文件损坏"这三类真实事故。
    """
    checks: list[tuple[bool, str]] = []

    def add(passed: bool, msg: str, detail: str = "") -> None:
        checks.append((passed, msg if passed else f"{msg}（{detail}）"))

    if not archive.exists():
        return [(False, f"备份文件不存在（{archive}）")]

    try:
        with tempfile.TemporaryDirectory() as tmp:
            with tarfile.open(archive, "r:gz") as tar:
                names = tar.getnames()
                tar.extractall(tmp, filter="data")
            add(
                any(n.endswith("manifest.json") for n in names),
                "归档可读且含 manifest.json",
                f"{len(names)} 个条目",
            )
            root = Path(tmp) / "einstein"
            manifest_path = root / "manifest.json"
            if not manifest_path.exists():
                return checks + [(False, "找不到 manifest.json，无法逐项核对")]

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            files = [f for f in manifest.get("files", []) if not f.endswith("/")]
            add(bool(files), "manifest 记录了内容文件", "files 为空")

            missing = [f for f in files if not (root / f).exists()]
            add(not missing, "manifest 列出的文件都在归档里", "、".join(missing[:5]))

            empty = [f for f in files if (root / f).exists() and (root / f).stat().st_size == 0]
            add(not empty, "没有 0 字节文件", "、".join(empty[:5]))

            backend = manifest.get("backend", "")
            if backend == "sqlite" and (root / "einstein.db").exists():
                conn = sqlite3.connect(f"file:{root / 'einstein.db'}?mode=ro", uri=True)
                try:
                    result = conn.execute("PRAGMA integrity_check").fetchone()
                    add(result and result[0] == "ok", "sqlite integrity_check 通过",
                        str(result[0] if result else ""))
                    tables = conn.execute(
                        "SELECT count(*) FROM sqlite_master WHERE type='table'"
                    ).fetchone()[0]
                    add(tables > 0, "sqlite 里有表", "0 张表")
                finally:
                    conn.close()
            elif backend == "json":
                bad: list[str] = []
                for name in files:
                    if not name.endswith(".json"):
                        continue
                    try:
                        json.loads((root / name).read_text(encoding="utf-8"))
                    except Exception:  # noqa: BLE001
                        bad.append(name)
                add(not bad, "json 文件都能解析", "、".join(bad[:5]))
            elif backend == "postgres":
                dump = root / "postgres.sql"
                if dump.exists():
                    head = dump.read_text(encoding="utf-8", errors="replace")[:200_000]
                    add("PostgreSQL database dump" in head, "pg_dump 文件头正常")
                    add(
                        "COPY " in head or "CREATE TABLE" in head,
                        "dump 里含表数据（COPY / CREATE TABLE）",
                    )
                    add(dump.stat().st_size > 1024, "dump 不是空壳",
                        f"{dump.stat().st_size} 字节")
    except (tarfile.TarError, OSError) as exc:
        return checks + [(False, f"归档打不开（{exc}）")]

    return checks


def restore(archive: Path, backend: str, pg_container: str, yes: bool) -> None:
    if not archive.exists():
        raise SystemExit(f"备份文件不存在：{archive}")
    if not yes:
        raise SystemExit("恢复会覆盖现有数据，加 --yes 确认")

    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(tmp, filter="data")
        root = Path(tmp) / "einstein"
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        print(f"备份创建于 {manifest.get('created_at')}，后端 {manifest.get('backend')}")

        if backend == "json":
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            for name in manifest["files"]:
                if name.endswith("/"):
                    continue
                shutil.copy2(root / name, DATA_DIR / name)
                print(f"  还原 {name}")
        elif backend == "sqlite":
            from app.core.config import get_settings

            dst = Path(get_settings().sqlite_path)
            if not dst.is_absolute():
                dst = SERVER_ROOT / dst
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / "einstein.db", dst)
            print(f"  还原 {dst}")
        elif backend == "postgres":
            _restore_postgres(root / "postgres.sql", pg_container)
            print("  已通过 psql 还原")
        else:
            raise SystemExit(f"未知后端：{backend}")

        if (root / "uploads").exists():
            target = DATA_DIR / "uploads"
            shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(root / "uploads", target)
            print("  还原 uploads/")

    print("恢复完成。重启后端进程以加载新数据。")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["backup", "restore", "list", "verify", "prune"])
    parser.add_argument("--backend", choices=["json", "sqlite", "postgres"], default=None)
    parser.add_argument("--pg-container", default="einstein-postgres")
    parser.add_argument("--file", default=None,
                        help="restore / verify 时的备份文件（verify 也接受目录，全部检查）")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--keep", type=int, default=14, help="prune 保留份数（默认 14）")
    parser.add_argument("--days", type=int, default=0, help="prune 额外删除超过 N 天的备份")
    parser.add_argument("--prune", type=int, default=0,
                        help="backup 成功后顺带按 N 份保留清理（0 = 不清理）")
    args = parser.parse_args()

    from app.core.config import get_settings

    backend = args.backend or (
        get_settings().store_backend if get_settings().store_backend != "json" else "json"
    )

    if args.action == "list":
        rows = _backup_files()
        if not rows:
            print("还没有备份")
            return 0
        total = sum(p.stat().st_size for p in rows)
        now = datetime.now()
        for path in rows:
            age_days = (now - datetime.fromtimestamp(path.stat().st_mtime)).total_seconds() / 86400
            print(f"{path.name}  {path.stat().st_size / 1024:>9.0f} KB  {age_days:.1f} 天前")
        print(f"共 {len(rows)} 份，合计 {total / 1024 / 1024:.1f} MB；目录 {BACKUP_DIR}")
        return 0

    if args.action == "backup":
        backup(backend, args.pg_container)
        if args.prune > 0:
            dropped, freed = prune(keep=args.prune, days=args.days)
            print(f"清理 {dropped} 份旧备份，释放 {freed / 1024 / 1024:.1f} MB")
        return 0

    if args.action == "prune":
        dropped, freed = prune(keep=args.keep, days=args.days)
        print(f"清理 {dropped} 份旧备份，释放 {freed / 1024 / 1024:.1f} MB")
        return 0

    if args.action == "verify":
        if not args.file:
            print("verify 需要用 --file 指定备份文件或目录", file=sys.stderr)
            return 2
        target = Path(args.file)
        archives = sorted(target.glob("einstein-*.tar.gz")) if target.is_dir() else [target]
        if not archives:
            print(f"{target} 下没有备份文件", file=sys.stderr)
            return 2
        failed = 0
        for archive in archives:
            print(f"\n== {archive.name} ==")
            checks = verify(archive)
            for passed, msg in checks:
                print(f"  {'PASS' if passed else 'FAIL'}  {msg}")
            if not all(p for p, _ in checks):
                failed += 1
        print(f"\n{len(archives) - failed}/{len(archives)} 份通过校验")
        return 1 if failed else 0

    if not args.file:
        print("restore 需要用 --file 指定备份文件", file=sys.stderr)
        return 2
    restore(Path(args.file), backend, args.pg_container, args.yes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
