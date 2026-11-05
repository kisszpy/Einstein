"""智能体沙箱：项目目录、路径安全、脚手架模板。

所有文件类能力的落点在 `data/projects/<workspace_id>/<project_id>/`，
工具只能看到这个子树。路径解析统一走 `resolve()`（拒绝 ../ 穿越、
拒绝绝对路径、拒绝驱动符），并用 `is_relative_to` 做最终校验——
这层校验不依赖字符串前缀匹配，符号链接也会被真实路径比对挡住。
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from app.services.store import DATA_DIR

PROJECTS_DIR = DATA_DIR / "projects"

# 单文件写入上限 / 单项目占用上限
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_PROJECT_BYTES = 64 * 1024 * 1024
MAX_TREE_ENTRIES = 400

_SLUG_RE = re.compile(r"[^a-zA-Z0-9\u4e00-\u9fff_-]+")
RESERVED = {"", ".", ".."}


class SandboxError(Exception):
    """沙箱内的可预期错误，消息直接回给模型。"""


def slugify(name: str) -> str:
    s = _SLUG_RE.sub("-", (name or "").strip()).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return s[:48] or "untitled"


def workspace_root(workspace_id: str) -> Path:
    root = PROJECTS_DIR / slugify(workspace_id or "default")
    root.mkdir(parents=True, exist_ok=True)
    return root


def project_dir(workspace_id: str, project: str) -> Path:
    """解析项目目录；不存在时抛错（不隐式创建，避免拼错路径写进新目录）。"""
    pid = slugify(project or "")
    if pid in RESERVED:
        raise SandboxError("项目名无效")
    target = workspace_root(workspace_id) / pid
    if not target.is_dir():
        raise SandboxError(f"项目 {pid} 不存在，请先用 project_create 创建")
    return target


def resolve_in(root: Path, rel: str) -> Path:
    """把相对路径解析到 root 之内，拒绝一切逃逸写法。"""
    raw = (rel or "").strip().replace("\\", "/")
    if raw.startswith("/") or re.match(r"^[a-zA-Z]:", raw):
        raise SandboxError("只接受项目内的相对路径")
    if any(part == ".." for part in raw.split("/")):
        raise SandboxError("路径不允许包含 ..")
    target = (root / raw).resolve() if raw else root.resolve()
    base = root.resolve()
    if target != base and not target.is_relative_to(base):
        raise SandboxError("路径越界")
    return target


def _project_bytes(root: Path) -> int:
    total = 0
    for p in root.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                continue
    return total


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


# ---------- 项目 ----------

_TEMPLATES: dict[str, dict[str, str]] = {
    "blank": {"README.md": "# {title}\n\n{description}\n"},
    "python": {
        "README.md": "# {title}\n\n{description}\n\n## 运行\n\n```bash\npython main.py\n```\n",
        "main.py": '""" {title} """\n\n\ndef main() -> None:\n    print("hello")\n\n\nif __name__ == "__main__":\n    main()\n',
        "requirements.txt": "",
    },
    "fastapi": {
        "README.md": "# {title}\n\n{description}\n\n## 运行\n\n```bash\npip install -r requirements.txt\nuvicorn main:app --reload\n```\n",
        "main.py": (
            'from fastapi import FastAPI\n\napp = FastAPI(title="{title}")\n\n\n'
            '@app.get("/health")\ndef health() -> dict:\n    return {{"ok": True}}\n'
        ),
        "requirements.txt": "fastapi\nuvicorn[standard]\n",
    },
    "web": {
        "README.md": "# {title}\n\n{description}\n\n直接用浏览器打开 index.html。\n",
        "index.html": (
            '<!doctype html>\n<html lang="zh-CN">\n<head>\n'
            '  <meta charset="utf-8" />\n  <meta name="viewport" content="width=device-width, initial-scale=1" />\n'
            "  <title>{title}</title>\n  <link rel=\"stylesheet\" href=\"style.css\" />\n</head>\n"
            "<body>\n  <main>\n    <h1>{title}</h1>\n    <p>{description}</p>\n  </main>\n"
            '  <script src="app.js"></script>\n</body>\n</html>\n'
        ),
        "style.css": (
            ":root { color-scheme: light dark; }\n"
            "body { margin: 0; font-family: system-ui, sans-serif; display: grid; place-items: center; min-height: 100vh; }\n"
            "main { max-width: 42rem; padding: 2rem; }\n"
        ),
        "app.js": 'console.log("{title} ready");\n',
    },
}

TEMPLATES = tuple(_TEMPLATES)


def create_project(
    workspace_id: str, name: str, template: str = "blank", description: str = ""
) -> dict:
    pid = slugify(name)
    if pid in RESERVED:
        raise SandboxError("项目名无效")
    if template not in _TEMPLATES:
        raise SandboxError(f"未知模板，可选：{', '.join(TEMPLATES)}")
    root = workspace_root(workspace_id) / pid
    existed = root.is_dir()
    root.mkdir(parents=True, exist_ok=True)
    files = _TEMPLATES[template]
    ctx = {"title": name.strip() or pid, "description": description.strip() or "（待补充）"}
    written: list[str] = []
    for rel, content in files.items():
        target = resolve_in(root, rel)
        if target.exists():
            continue  # 幂等：不覆盖已有文件
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content.format(**ctx), encoding="utf-8")
        written.append(rel)
    return {
        "project": pid,
        "path": f"projects/{slugify(workspace_id or 'default')}/{pid}",
        "template": template,
        "created": not existed,
        "files_created": written,
    }


def list_projects(workspace_id: str) -> list[dict]:
    root = workspace_root(workspace_id)
    out: list[dict] = []
    for p in sorted(root.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if not p.is_dir():
            continue
        stat = p.stat()
        out.append(
            {
                "id": p.name,
                "name": p.name,
                "files": sum(1 for f in p.rglob("*") if f.is_file()),
                "size": human_size(_project_bytes(p)),
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
            }
        )
    return out


def delete_project(workspace_id: str, project: str) -> bool:
    root = project_dir(workspace_id, project)
    shutil.rmtree(root, ignore_errors=True)
    return not root.exists()


# ---------- 文件 ----------

_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", ".agent_tmp"}


def write_file(workspace_id: str, project: str, rel: str, content: str) -> dict:
    root = project_dir(workspace_id, project)
    data = (content or "").encode("utf-8")
    if len(data) > MAX_FILE_BYTES:
        raise SandboxError(f"单文件超过 {human_size(MAX_FILE_BYTES)} 上限")
    target = resolve_in(root, rel)
    if target.is_dir():
        raise SandboxError("目标路径是目录")
    if _project_bytes(root) + len(data) > MAX_PROJECT_BYTES:
        raise SandboxError(f"项目总体积超过 {human_size(MAX_PROJECT_BYTES)} 上限")
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    target.write_bytes(data)
    return {
        "path": rel,
        "bytes": len(data),
        "created": not existed,
        "total_files": sum(1 for f in root.rglob("*") if f.is_file()),
    }


def read_file(workspace_id: str, project: str, rel: str, max_chars: int = 12000) -> dict:
    root = project_dir(workspace_id, project)
    target = resolve_in(root, rel)
    if not target.is_file():
        raise SandboxError(f"文件不存在：{rel}")
    if target.stat().st_size > MAX_FILE_BYTES:
        raise SandboxError("文件过大，无法读取")
    raw = target.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"path": rel, "binary": True, "bytes": len(raw), "text": "（二进制文件，不展示内容）"}
    truncated = len(text) > max_chars
    return {
        "path": rel,
        "binary": False,
        "bytes": len(raw),
        "truncated": truncated,
        "text": text[:max_chars],
        "total_chars": len(text),
    }


def list_files(workspace_id: str, project: str, rel: str = "") -> dict:
    root = project_dir(workspace_id, project)
    base = resolve_in(root, rel)
    if not base.exists():
        raise SandboxError(f"路径不存在：{rel}")
    entries: list[dict] = []

    def walk(node: Path, prefix: str) -> None:
        if len(entries) >= MAX_TREE_ENTRIES:
            return
        children = sorted(node.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        for child in children:
            if len(entries) >= MAX_TREE_ENTRIES:
                return
            if child.name in _SKIP_DIRS:
                continue
            relpath = f"{prefix}{child.name}"
            if child.is_dir():
                entries.append({"path": relpath, "type": "dir"})
                walk(child, relpath + "/")
            else:
                try:
                    size = child.stat().st_size
                except OSError:
                    size = 0
                entries.append({"path": relpath, "type": "file", "size": human_size(size)})

    if base.is_file():
        entries.append({"path": rel, "type": "file", "size": human_size(base.stat().st_size)})
    else:
        walk(base, f"{rel.rstrip('/')}/" if rel else "")
    return {"project": project, "root": rel or ".", "entries": entries, "count": len(entries)}


def project_summary(workspace_id: str, project: str) -> dict:
    root = project_dir(workspace_id, project)
    files = [p for p in root.rglob("*") if p.is_file()]
    artifacts = [p for p in files if p.suffix.lower() in {".pptx", ".pdf", ".docx", ".xlsx", ".png", ".svg", ".html", ".md"}]
    return {
        "project": project,
        "files": len(files),
        "size": human_size(_project_bytes(root)),
        "artifacts": [
            {
                "path": str(p.relative_to(root)).replace("\\", "/"),
                "size": human_size(p.stat().st_size),
                "name": p.name,
            }
            for p in sorted(artifacts, key=lambda x: x.stat().st_mtime, reverse=True)[:20]
        ],
    }


def dumps(payload: dict) -> str:
    """工具返回值统一 JSON 字符串，模型解析稳定。"""
    return json.dumps(payload, ensure_ascii=False, indent=2)
