"""下载并校验 impeccable 检测引擎（Apache-2.0，pbakaus/impeccable）。

二进制不入库（20MB/平台，五个平台就是 100MB），只入库锁定的版本与 sha256。
校验用 upstream 的 engine-checksums.txt，不用 release 旁边的 .sha256——后者
和二进制同一个来源，被替换时两个会一起变，验不出东西。

    python tools/impeccable/fetch.py
"""

from __future__ import annotations

import hashlib
import platform
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "engine-checksums.txt"
BASE = "https://github.com/pbakaus/impeccable/releases/download"


def _target() -> str:
    system = {"Windows": "windows", "Linux": "linux", "Darwin": "darwin"}.get(platform.system())
    arch = {"AMD64": "x64", "x86_64": "x64", "ARM64": "arm64", "aarch64": "arm64"}.get(platform.machine())
    if not system or not arch:
        raise SystemExit(f"没有对应平台的引擎：{platform.system()}-{platform.machine()}")
    return f"{system}-{arch}"


def _rows() -> list[tuple[str, str, str]]:
    rows = []
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        version, asset, digest = line.split()
        rows.append((version, asset, digest.lower()))
    return rows


def fetch(target: str) -> Path:
    suffix = ".exe" if target.startswith("windows") else ""
    wanted = f"impeccable-{target}{suffix}"
    row = next((r for r in _rows() if r[1] == wanted), None)
    if row is None:
        raise SystemExit(f"{MANIFEST.name} 里没有 {wanted} 的校验和，拒绝下载")
    version, _, digest = row
    dest = HERE / "bin" / f"impeccable{suffix}"
    if dest.is_file() and hashlib.sha256(dest.read_bytes()).hexdigest() == digest:
        print(f"已就位 {dest}（engine v{version}）")
        return dest
    url = f"{BASE}/engine-v{version}/{wanted}"
    print(f"下载 {url}")
    with urllib.request.urlopen(url, timeout=180) as response:
        blob = response.read()
    actual = hashlib.sha256(blob).hexdigest()
    if actual != digest:
        raise SystemExit(f"校验和不符，已丢弃：期望 {digest}，实得 {actual}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(blob)
    dest.chmod(0o755)
    print(f"校验通过 {dest}（{len(blob) / 1048576:.1f}MB，engine v{version}）")
    return dest


def main() -> int:
    fetch(_target())
    return 0


if __name__ == "__main__":
    sys.exit(main())
