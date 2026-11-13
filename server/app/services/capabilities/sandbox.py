"""智能体沙箱：项目目录、路径安全、脚手架模板。

所有文件类能力的落点在 `data/projects/<workspace_id>/<project_id>/`，
工具只能看到这个子树。路径解析统一走 `resolve()`（拒绝 ../ 穿越、
拒绝绝对路径、拒绝驱动符），并用 `is_relative_to` 做最终校验——
这层校验不依赖字符串前缀匹配，符号链接也会被真实路径比对挡住。
"""

from __future__ import annotations

import base64
import json
import os
import re
import stat as _stat
from collections.abc import Iterator
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


def slug_from_name(name: str) -> str:
    """清洗成目录名；可能返回空串（纯 emoji / 纯符号）。

    与 `slugify` 分开是为了让「名字不可用」这件事能被上层拒绝。以前两者合
    成一个函数，空值回落 `untitled`，于是 `🚀`、`+++` 全落到同一个目录，
    第二个项目直接复用第一个（用户看到的是"我的项目串了"）。
    """
    s = _SLUG_RE.sub("-", (name or "").strip()).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return s[:48]


def slugify(name: str) -> str:
    """清洗成目录名，空值回落 untitled。

    只用于**内部路径**（工作区名等）。用户给的项目名必须先过
    `slug_from_name` 判空（见 projects.create_project）。
    """
    return slug_from_name(name) or "untitled"


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


def _stat_safe(p: Path) -> os.stat_result | None:
    """stat 失败返回 None：Windows 对悬空符号链接（如 .deb 里解出的 Linux 软链）
    抛 OSError(WinError 1920)，pathlib 的 is_file()/is_dir() 会直接炸。"""
    try:
        return p.stat()
    except OSError:
        return None


def iter_files(root: Path) -> Iterator[tuple[Path, os.stat_result]]:
    """递归枚举普通文件及其 stat；不可访问的条目跳过而不是抛错。

    背景：智能体解包 .deb / tar 会在项目里留下 Linux 软链，Windows 对这类
    条目 stat 抛 OSError(WinError 1920)，`Path.is_file()` 直接炸——
    /api/projects 曾因此全量 500（nocturne-cinema 里的 libatomic1）。
    所有递归统计/打包/列树一律走这里，不要在项目目录上裸 rglob。
    """
    for dirpath, _dirnames, filenames in os.walk(root, onerror=lambda e: None):
        for name in filenames:
            path = Path(dirpath) / name
            st = _stat_safe(path)
            if st is not None and _stat.S_ISREG(st.st_mode):
                yield path, st


def dir_size(root: Path) -> int:
    """目录内所有文件字节数（配额与列表展示共用）。"""
    return sum(st.st_size for _p, st in iter_files(root))


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


def _render(content: str, ctx: dict[str, str]) -> str:
    """只替换 {title}/{description} 这类占位符。

    不能用 str.format——模板里有 CSS（`:root { color-scheme: ... }`）和
    JS 花括号，format 会把它们当字段名并抛 KeyError。
    """
    for key, value in ctx.items():
        content = content.replace("{" + key + "}", value)
    return content


def scaffold_project(
    workspace_id: str, slug: str, template: str = "blank", description: str = ""
) -> dict:
    """按模板铺一份项目骨架。只管文件，不登记项目（登记在 services/projects.py）。"""
    pid = slugify(slug)
    if pid in RESERVED:
        raise SandboxError("项目名无效")
    if template not in _TEMPLATES:
        raise SandboxError(f"未知模板，可选：{', '.join(TEMPLATES)}")
    root = workspace_root(workspace_id) / pid
    existed = root.is_dir()
    root.mkdir(parents=True, exist_ok=True)
    files = _TEMPLATES[template]
    ctx = {"title": pid, "description": description.strip() or "（待补充）"}
    written: list[str] = []
    for rel, content in files.items():
        target = resolve_in(root, rel)
        if target.exists():
            continue  # 幂等：不覆盖已有文件
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_render(content, ctx), encoding="utf-8")
        written.append(rel)
    return {
        "project": pid,
        "path": f"projects/{slugify(workspace_id or 'default')}/{pid}",
        "template": template,
        "created": not existed,
        "files_created": written,
    }


# ---------- 文件 ----------

# .deck 存演示文稿的可编辑来源（sidecar），属于实现细节，不进文件树与打包；
# .agent 是工作区级的运行时目录（技能同步、临时沙盒），同样不属于用户内容。
# 后三个是**第三方解包目录**：技能/依赖被整包解开时会出现成百上千个文件，
# 它们不是用户的产物，必须在这里挡掉（nocturne-cinema 的 libx 就是整套
# Linux sysroot，1121 个文件里真正的产物不到 10 个）。
SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv",
    ".agent", ".agent_tmp", ".deck", ".deckpreview", ".slidep",
    "libx", "vendor", "site-packages", "dist-packages",
    ".idea", ".vscode", ".next", ".nuxt", ".pytest_cache",
}

# 浏览页「产物」的白名单：既包括可预览的成品，也包括工程源码。
#
# 为什么源码也要算产物：用户让智能体「搭一套 Spring Cloud 工程」时，交付物
# 就是那几十个 .java/.xml/.yml。只白名单 .md/.pptx 会把整个项目判成「没有
# 产物」——实测 ecommerce-cloud 43 个文件只剩 2 个 markdown 可见，用户以为
# 文件根本没同步回来。
#
# 仍然靠 SKIP_DIRS 挡掉依赖目录，并刻意不收 .c/.h/.so 这类编译中间件，
# 避免解包出来的 sysroot 再次把产物淹没。
ARTIFACT_SUFFIXES = {
    # 成品：能直接预览/交付
    ".html", ".htm", ".md", ".pptx", ".pdf", ".docx", ".xlsx",
    ".png", ".jpg", ".jpeg", ".webp", ".svg", ".csv", ".txt",
    # 工程源码与配置：整份代码本身就是交付物
    ".java", ".kt", ".gradle", ".xml", ".yml", ".yaml", ".properties", ".sql",
    ".js", ".jsx", ".ts", ".tsx", ".vue", ".css", ".scss", ".json",
    ".py", ".go", ".rs", ".cs", ".php", ".rb", ".sh", ".toml", ".ini", ".conf",
}


def write_file(workspace_id: str, project: str, rel: str, content: str) -> dict:
    root = project_dir(workspace_id, project)
    data = (content or "").encode("utf-8")
    if len(data) > MAX_FILE_BYTES:
        raise SandboxError(f"单文件超过 {human_size(MAX_FILE_BYTES)} 上限")
    target = resolve_in(root, rel)
    if target.is_dir():
        raise SandboxError("目标路径是目录")
    if dir_size(root) + len(data) > MAX_PROJECT_BYTES:
        raise SandboxError(f"项目总体积超过 {human_size(MAX_PROJECT_BYTES)} 上限")
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    target.write_bytes(data)
    return {
        "path": rel,
        "bytes": len(data),
        "created": not existed,
        "total_files": sum(1 for _f, _st in iter_files(root)),
    }


# 成品二进制（pptx/docx/图片）单独设上限：2MB 的文本上限装不下真实演示，
# 但也不能照搬项目的 64MB——一次 PUT 就把内存吃掉不值当。
MAX_BINARY_BYTES = 32 * 1024 * 1024
BINARY_SUFFIXES = {
    ".pptx", ".docx", ".xlsx", ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".svg",
}


def write_bytes(workspace_id: str, project: str, rel: str, content_base64: str) -> dict:
    """写入二进制成品，内容以 base64 传入（pptx 是 zip，不能走 UTF-8 文本那条路）。

    只认 `BINARY_SUFFIXES`：这是「浏览器里编辑演示后保存回来」的通道，
    不该变成任意文件上传口。base64 解码放在这里而不是路由层，是为了
    让体积校验与路径校验在同一处失败、报同一个 `SandboxError`。
    """
    root = project_dir(workspace_id, project)
    suffix = Path(rel).suffix.lower()
    if suffix not in BINARY_SUFFIXES:
        raise SandboxError(f"不支持写入该类型：{suffix or '无后缀'}")
    try:
        raw = base64.b64decode(content_base64, validate=True)
    except (ValueError, TypeError) as exc:
        raise SandboxError("内容不是合法的 base64") from exc
    if not raw:
        raise SandboxError("内容为空")
    if len(raw) > MAX_BINARY_BYTES:
        raise SandboxError(f"单文件超过 {human_size(MAX_BINARY_BYTES)} 上限")
    target = resolve_in(root, rel)
    if target.is_dir():
        raise SandboxError("目标路径是目录")
    existed = target.exists()
    previous = target.stat().st_size if existed else 0
    # 覆盖写不该因为旧文件占位而超限：先把旧体积从总量里扣掉
    if dir_size(root) - previous + len(raw) > MAX_PROJECT_BYTES:
        raise SandboxError(f"项目总体积超过 {human_size(MAX_PROJECT_BYTES)} 上限")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    return {
        "path": rel,
        "bytes": len(raw),
        "previous_bytes": previous,
        "created": not existed,
        "total_files": sum(1 for _f, _st in iter_files(root)),
    }


def delete_file(workspace_id: str, project: str, rel: str) -> dict:
    """删除项目内的单个文件。

    只删文件不删目录：目录删除会把「删一项」变成「删一片」，那是另一个动作，
    要有自己的确认与范围提示，不能藏在文件的删除按钮后面。
    """
    root = project_dir(workspace_id, project)
    if not (rel or "").strip():
        raise SandboxError("必须指定要删除的文件")
    target = resolve_in(root, rel)
    stat = _stat_safe(target)
    if stat is None:
        raise SandboxError("文件不存在")
    if target.is_dir():
        raise SandboxError("只能删除文件，不能删除目录")
    target.unlink()
    return {
        "path": rel,
        "bytes": stat.st_size,
        "total_files": sum(1 for _f, _st in iter_files(root)),
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
        try:
            children = list(node.iterdir())
        except OSError:
            return  # 目录本身不可读：这层留空，不让整棵树 500
        # 先分类再排序：dirs 在前、files 在后（保持原顺序语义）；
        # 不可访问条目（悬空软链等）直接跳过，不进树
        dirs: list[Path] = []
        files: list[tuple[Path, os.stat_result]] = []
        for child in children:
            if child.name in SKIP_DIRS:
                continue
            st = _stat_safe(child)
            if st is None:
                continue
            if _stat.S_ISDIR(st.st_mode):
                dirs.append(child)
            else:
                files.append((child, st))
        dirs.sort(key=lambda p: p.name.lower())
        files.sort(key=lambda item: item[0].name.lower())
        for child in dirs:
            if len(entries) >= MAX_TREE_ENTRIES:
                return
            relpath = f"{prefix}{child.name}"
            entries.append({"path": relpath, "type": "dir"})
            walk(child, relpath + "/")
        for child, st in files:
            if len(entries) >= MAX_TREE_ENTRIES:
                return
            entries.append({"path": f"{prefix}{child.name}", "type": "file", "size": human_size(st.st_size)})

    if base.is_file():
        entries.append({"path": rel, "type": "file", "size": human_size(base.stat().st_size)})
    else:
        walk(base, f"{rel.rstrip('/')}/" if rel else "")
    return {"project": project, "root": rel or ".", "entries": entries, "count": len(entries)}


def _iter_artifacts(root: Path) -> Iterator[tuple[Path, str, os.stat_result]]:
    """枚举产物文件（路径, 项目内相对路径, stat），浏览页与 project_summary 共用。

    排除三类非产物：非白名单后缀、SKIP_DIRS 依赖目录（node_modules 等）、
    点/下划线开头的运行时目录（.cache、.local、_scratch…——智能体 pip
    install/解包留下的东西，里面即使有 html/png 也不是用户的产物）。
    """
    for p, st in iter_files(root):
        if p.suffix.lower() not in ARTIFACT_SUFFIXES:
            continue
        rel = p.relative_to(root).as_posix()
        parts = rel.split("/")
        if set(parts) & SKIP_DIRS:
            continue
        if any(part.startswith((".", "_")) for part in parts):
            continue
        yield p, rel, st


def count_artifacts(root: Path) -> int:
    """产物文件数（项目列表展示用），判定与 list_artifacts 完全一致。

    /api/projects 的 files 是全量文件数，浏览页产物清单是白名单过滤后的
    数量——两处数字曾经对不上（nocturne-cinema 1121 vs 2），这个字段让
    选择器括号里的数字和清单条数同源。
    """
    return sum(1 for _p, _rel, _st in _iter_artifacts(root))


def list_artifacts(workspace_id: str, project: str) -> dict:
    """浏览页的产物清单：只列 ARTIFACT_SUFFIXES 白名单内的成品文件。

    判定与 project_summary 用同一套规则，两处永远一致。返回形状与
    list_files 相同（entries 平铺、带 size），前端 FileTree 无需适配。
    """
    root = project_dir(workspace_id, project)
    entries = [
        {"path": rel, "type": "file", "size": human_size(st.st_size)}
        for _p, rel, st in _iter_artifacts(root)
    ]
    entries.sort(key=lambda item: item["path"])
    truncated = len(entries) > MAX_TREE_ENTRIES
    if truncated:
        entries = entries[:MAX_TREE_ENTRIES]
    return {
        "project": project,
        "root": ".",
        "entries": entries,
        "count": len(entries),
        "truncated": truncated,
    }


def project_summary(workspace_id: str, project: str) -> dict:
    root = project_dir(workspace_id, project)
    listing = list(iter_files(root))
    artifacts = [(p, st) for p, _rel, st in _iter_artifacts(root)]
    return {
        "project": project,
        "files": len(listing),
        "size": human_size(sum(st.st_size for _p, st in listing)),
        "artifacts": [
            {
                "path": str(p.relative_to(root)).replace("\\", "/"),
                "size": human_size(st.st_size),
                "name": p.name,
            }
            for p, st in sorted(artifacts, key=lambda x: x[1].st_mtime, reverse=True)[:20]
        ],
    }


def dumps(payload: dict) -> str:
    """工具返回值统一 JSON 字符串，模型解析稳定。"""
    return json.dumps(payload, ensure_ascii=False, indent=2)
