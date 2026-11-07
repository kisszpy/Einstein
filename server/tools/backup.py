"""备份与恢复。

覆盖三种存储后端：
- json    → 打包 server/data 下的状态文件
- sqlite  → 用 sqlite3 backup API 复制（在线安全，不锁库）
- postgres→ pg_dump（优先走容器，可用 --pg-container 指定）

用法：
    python tools/backup.py backup                       # 按当前 APP_STORE_BACKEND
    python tools/backup.py backup --backend postgres --pg-container einstein-postgres
    python tools/backup.py list
    python tools/backup.py restore backups/einstein-20261107-130000.tar.gz --yes
"""

from __future__ import annotations

import argparse
import json
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
    parser.add_argument("action", choices=["backup", "restore", "list"])
    parser.add_argument("--backend", choices=["json", "sqlite", "postgres"], default=None)
    parser.add_argument("--pg-container", default="einstein-postgres")
    parser.add_argument("--file", default=None, help="restore 时的备份文件")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()

    from app.core.config import get_settings

    backend = args.backend or (
        get_settings().store_backend if get_settings().store_backend != "json" else "json"
    )

    if args.action == "list":
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        rows = sorted(BACKUP_DIR.glob("einstein-*.tar.gz"), reverse=True)
        if not rows:
            print("还没有备份")
            return 0
        for path in rows:
            print(f"{path.name}  {path.stat().st_size / 1024:.0f} KB")
        return 0

    if args.action == "backup":
        backup(backend, args.pg_container)
        return 0

    if not args.file:
        print("restore 需要用 --file 指定备份文件", file=sys.stderr)
        return 2
    restore(Path(args.file), backend, args.pg_container, args.yes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
