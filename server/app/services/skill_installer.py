"""从 GitHub 动态安装技能包。

遵循 Agent Skills 约定：一个技能是一个目录，根上有 `SKILL.md`（YAML frontmatter +
正文），其余是随包资源（references/scripts/assets 等）。

流程：解析地址 → 解析默认分支与 commit（作为版本）→ 用 Git Tree 接口列出文件 →
只抓取选中目录的原始文件 → 落盘 → 登记到技能库。不下载整仓压缩包（大仓库会
超时，也没必要），不执行任何代码，不跟随符号链接，文件数与体积都有硬上限。

生产替换点：对象存储存包文件，PostgreSQL 存元数据与版本历史。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from app.core.config import get_settings
from app.services.skills import Skill, SkillStore

SKILLS_DIR = Path(__file__).resolve().parents[2] / "data" / "skills"

MAX_SKILL_BYTES = 20 * 1024 * 1024
MAX_SKILL_FILES = 300
MAX_PROMPT_CHARS = 16000
MAX_CANDIDATES = 60

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next"}

_TREE_CACHE: dict[str, tuple[float, "RepoRef"]] = {}
_CACHE_TTL = 300
_cache_lock = threading.Lock()


class SkillInstallError(Exception):
    """安装失败：消息直接给用户看。"""


@dataclass
class RepoRef:
    repo: str
    ref: str
    commit: str
    default_branch: str = ""
    description: str = ""
    stars: int = 0
    license: str = ""
    author: str = ""

    def to_public(self) -> dict:
        return {
            "repo": self.repo,
            "ref": self.ref,
            "commit": self.commit,
            "commit_short": self.commit[:10],
            "description": self.description,
            "stars": self.stars,
            "license": self.license,
            "author": self.author,
            "url": f"https://github.com/{self.repo}",
        }


@dataclass
class SkillCandidate:
    path: str
    name: str
    description: str
    file_count: int = 0
    size_bytes: int = 0
    files: list[str] = field(default_factory=list)

    def to_public(self) -> dict:
        return {
            "path": self.path,
            "name": self.name,
            "description": self.description,
            "file_count": self.file_count,
            "size_bytes": self.size_bytes,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- HTTP ----------


def github_headers(accept: str = "application/vnd.github+json") -> dict[str, str]:
    headers = {
        "User-Agent": "einstein-skill-installer",
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = get_settings().github_token
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _request(
    url: str,
    timeout: int = 30,
    accept: str = "application/vnd.github+json",
    retries: int = 1,
) -> bytes:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=github_headers(accept))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise SkillInstallError("仓库、分支或文件不存在，请检查地址") from exc
            if exc.code in (403, 429):
                raise SkillInstallError(
                    "GitHub 接口限流，请稍后再试（服务端配置 APP_GITHUB_TOKEN 可提升额度）"
                ) from exc
            raise SkillInstallError(f"GitHub 返回 {exc.code}，请稍后再试") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1 + attempt)
                continue
    raise SkillInstallError("连接 GitHub 超时，请稍后重试或检查网络") from last_error


def github_json(url: str, timeout: int = 30) -> dict:
    try:
        return json.loads(_request(url, timeout=timeout))
    except json.JSONDecodeError as exc:  # pragma: no cover — 理论上不会发生
        raise SkillInstallError("GitHub 返回了无法解析的内容") from exc


# ---------- 地址解析 ----------


def parse_github_url(raw: str) -> tuple[str, str, str]:
    """把地址解析成 (owner/repo, ref, 子目录)。

    支持：
      https://github.com/owner/repo
      https://github.com/owner/repo/tree/main/skills/foo
      owner/repo
    """
    text = (raw or "").strip()
    if not text:
        raise SkillInstallError("请输入 GitHub 仓库地址")
    text = re.sub(r"^git\+", "", text)
    if text.startswith("git@github.com:"):
        text = "https://github.com/" + text[len("git@github.com:") :]

    if re.fullmatch(r"[\w.-]+/[\w.-]+", text):
        return text, "", ""

    match = re.match(r"^https?://(?:www\.)?github\.com/([^/]+)/([^/#?]+)(.*)$", text)
    if not match:
        raise SkillInstallError("仅支持 GitHub 仓库地址，例如 https://github.com/owner/repo")

    owner, name, rest = match.group(1), match.group(2), match.group(3)
    if name.endswith(".git"):
        name = name[:-4]
    repo = f"{owner}/{name}"

    rest = rest.split("?")[0].split("#")[0].strip("/")
    if not rest:
        return repo, "", ""
    if rest.startswith("blob/"):
        raise SkillInstallError("请填写仓库或技能目录地址，不要填单个文件")
    if rest.startswith("tree/"):
        parts = rest[len("tree/") :].split("/", 1)
        return repo, parts[0], (parts[1] if len(parts) > 1 else "")
    raise SkillInstallError("无法识别的地址格式，请填写仓库根地址或 /tree/<分支>/<目录>")


def resolve_repo(repo: str, ref: str = "") -> RepoRef:
    key = f"{repo}@{ref}"
    with _cache_lock:
        hit = _TREE_CACHE.get(key)
        if hit and time.monotonic() - hit[0] < _CACHE_TTL:
            return hit[1]

    meta = github_json(f"https://api.github.com/repos/{repo}")
    default_branch = str(meta.get("default_branch") or "main")
    target = ref or default_branch
    commits = github_json(
        f"https://api.github.com/repos/{repo}/commits?"
        f"sha={urllib.parse.quote(target)}&per_page=1"
    )
    if not isinstance(commits, list) or not commits:
        raise SkillInstallError(f"分支或标签不存在：{target}")

    info = RepoRef(
        repo=repo,
        ref=target,
        commit=str(commits[0]["sha"]),
        default_branch=default_branch,
        description=str(meta.get("description") or ""),
        stars=int(meta.get("stargazers_count") or 0),
        license=str((meta.get("license") or {}).get("spdx_id") or ""),
        author=str((meta.get("owner") or {}).get("login") or repo.split("/")[0]),
    )
    with _cache_lock:
        _TREE_CACHE[key] = (time.monotonic(), info)
    return info


# ---------- 仓库文件树 ----------


@dataclass
class TreeEntry:
    path: str
    size: int
    sha: str


class GithubTree:
    """按 commit 固定版本读取仓库文件，避免整仓压缩包下载。"""

    def __init__(self, info: RepoRef) -> None:
        self.info = info
        data = github_json(
            f"https://api.github.com/repos/{info.repo}/git/trees/{info.commit}?recursive=1",
            timeout=60,
        )
        if data.get("truncated"):
            raise SkillInstallError("仓库文件过多，无法完整扫描，请直接填写技能目录地址")
        self.entries: list[TreeEntry] = [
            TreeEntry(path=str(node["path"]), size=int(node.get("size") or 0), sha=str(node["sha"]))
            for node in data.get("tree", [])
            if node.get("type") == "blob"
        ]
        self.by_path = {entry.path: entry for entry in self.entries}

    def fetch(self, path: str, timeout: int = 45) -> bytes:
        if get_settings().github_token:  # 私有仓库需走 contents 接口鉴权
            url = (
                f"https://api.github.com/repos/{self.info.repo}/contents/"
                f"{urllib.parse.quote(path)}?ref={self.info.commit}"
            )
            return _request(url, timeout=timeout, accept="application/vnd.github.raw")
        url = (
            f"https://raw.githubusercontent.com/{self.info.repo}/"
            f"{self.info.commit}/{urllib.parse.quote(path)}"
        )
        return _request(url, timeout=timeout, accept="text/plain")

    def under(self, prefix: str) -> list[TreeEntry]:
        if not prefix:
            return sorted(self.entries, key=lambda e: e.path)
        head = prefix.rstrip("/") + "/"
        return sorted(
            (e for e in self.entries if e.path.startswith(head)), key=lambda e: e.path
        )

    def find_candidates(self) -> list[SkillCandidate]:
        skill_files: dict[str, str] = {}
        for entry in self.entries:
            if PurePosixPath(entry.path).name.lower() != "skill.md":
                continue
            parts = PurePosixPath(entry.path).parts
            if any(part in SKIP_DIRS for part in parts[:-1]):
                continue
            parent = str(PurePosixPath(entry.path).parent)
            parent = "" if parent == "." else parent
            if parent.count("/") > 3:  # 过深的示例/模板目录不作为独立技能
                continue
            skill_files[parent] = entry.path

        if not skill_files:
            return []

        # 仓库根本身就是技能时，不再把子目录当独立技能
        dirs = (
            [""]
            if "" in skill_files
            else sorted(skill_files, key=lambda p: (p.count("/"), p))[:MAX_CANDIDATES]
        )
        texts = self._fetch_many([skill_files[d] for d in dirs])

        candidates: list[SkillCandidate] = []
        for directory in dirs:
            files = self.under(directory)[:MAX_SKILL_FILES]
            raw = texts.get(skill_files[directory], b"").decode("utf-8", errors="replace")
            front, _ = parse_frontmatter(raw)
            candidates.append(
                SkillCandidate(
                    path=directory,
                    name=str(
                        front.get("name")
                        or (
                            directory.split("/")[-1]
                            if directory
                            else self.info.repo.split("/")[-1]
                        )
                    ),
                    description=str(front.get("description") or ""),
                    file_count=len(files),
                    size_bytes=sum(f.size for f in files),
                    files=[f.path for f in files],
                )
            )
        return candidates

    def collect(self, directory: str) -> dict[str, bytes]:
        entries = self.under(directory)
        if not entries:
            raise SkillInstallError("该目录下没有文件")
        if len(entries) > MAX_SKILL_FILES:
            raise SkillInstallError(f"技能文件过多（{len(entries)} 个，上限 {MAX_SKILL_FILES}）")
        total = sum(entry.size for entry in entries)
        if total > MAX_SKILL_BYTES:
            raise SkillInstallError(
                f"技能包过大（{total // (1024 * 1024)}MB，上限 {MAX_SKILL_BYTES // (1024 * 1024)}MB）"
            )

        prefix = directory.rstrip("/") + "/" if directory else ""
        fetched = self._fetch_many([entry.path for entry in entries])
        return {
            (path[len(prefix) :] if prefix else path): content
            for path, content in fetched.items()
        }

    def _fetch_many(self, paths: list[str]) -> dict[str, bytes]:
        if not paths:
            return {}
        results: dict[str, bytes] = {}

        def work(path: str) -> tuple[str, bytes]:
            return path, self.fetch(path)

        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = [pool.submit(work, path) for path in paths]
            for future in futures:
                path, content = future.result()
                results[path] = content
        return results


# ---------- SKILL.md ----------


def parse_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """解析极简 YAML frontmatter（只取顶层标量），返回 (字段, 正文)。"""
    normalized = text.lstrip("\ufeff")
    if not normalized.startswith("---"):
        return {}, normalized.strip()

    end = normalized.find("\n---", 3)
    if end == -1:
        return {}, normalized.strip()

    block = normalized[3:end]
    body = normalized[end + 4 :].lstrip("\n")
    fields: dict[str, object] = {}
    for raw_line in block.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")):  # 嵌套结构暂不展开
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
            value = value[1:-1]
        fields[key.strip()] = value
    return fields, body


def _skill_id(workspace_id: str, repo: str, subpath: str, owner_id: str = "") -> str:
    """技能 id = 工作区 + 仓库 + 目录（+ 归属）。

    归属拼进种子是必须的：同一空间的两个人装同一个技能，如果不区分归属会
    命中同一个 id、互相覆盖内容。预置安装 owner 为空，id 与历史一致。
    """
    seed = f"{workspace_id}|{repo}|{subpath}" + (f"|{owner_id}" if owner_id else "")
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()
    return f"gh-{digest[:10]}"


def _write_files(skill_id: str, payload: dict[str, bytes]) -> list[dict]:
    target = (SKILLS_DIR / skill_id).resolve()
    if SKILLS_DIR.resolve() not in target.parents:
        raise SkillInstallError("非法的技能目录")

    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)

    manifest: list[dict] = []
    for rel, content in payload.items():
        dest = (target / rel).resolve()
        if target not in dest.parents:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        manifest.append({"path": rel, "size": len(content)})
    return sorted(manifest, key=lambda item: item["path"])


def remove_files(skill_id: str) -> None:
    target = (SKILLS_DIR / skill_id).resolve()
    if SKILLS_DIR.resolve() not in target.parents:
        return
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)


def read_text_file(skill_id: str, rel_path: str) -> tuple[str, bool]:
    """读取技能包内文件，返回 (内容, 是否文本)。"""
    root = (SKILLS_DIR / skill_id).resolve()
    target = (root / rel_path).resolve()
    if root not in target.parents or not target.is_file():
        raise SkillInstallError("文件不存在")
    if target.stat().st_size > 2 * 1024 * 1024:
        raise SkillInstallError("文件过大，无法预览")
    raw = target.read_bytes()
    try:
        return raw.decode("utf-8"), True
    except UnicodeDecodeError:
        return "", False


def inline_resources(
    skill_id: str,
    files: list[dict],
    budget_chars: int = 6000,
    per_file_chars: int = 3000,
    max_files: int = 3,
) -> str:
    """把小体积文本资源内联进提示词（简易渐进式披露）。

    只取少量短文本，避免把上下文挤爆；大文件与二进制只做计数提示。
    """
    root = (SKILLS_DIR / skill_id).resolve()
    if not root.exists():
        return ""

    chunks: list[str] = []
    used = 0
    skipped = 0
    picked = 0
    for item in sorted(files, key=lambda f: f.get("path", "")):
        path = str(item.get("path", ""))
        if path.lower() == "skill.md":
            continue
        size = int(item.get("size") or 0)
        if picked >= max_files or size > per_file_chars * 4 or not path.lower().endswith(
            (".md", ".txt", ".json", ".yaml", ".yml", ".csv")
        ):
            skipped += 1
            continue
        target = (root / path).resolve()
        if root not in target.parents or not target.is_file():
            skipped += 1
            continue
        try:
            text = target.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            skipped += 1
            continue
        if len(text) > per_file_chars:
            text = text[:per_file_chars] + "\n…（已截断）"
        if used + len(text) > budget_chars:
            skipped += 1
            continue
        chunks.append(f"【技能资料 · {path}】\n{text}")
        used += len(text)
        picked += 1

    if skipped:
        chunks.append(f"（该技能另有 {skipped} 个资源文件未内联，可在技能页查看）")
    return "\n\n".join(chunks)


# ---------- 安装 ----------


def _plan(url: str, ref: str, path: str) -> tuple[RepoRef, GithubTree, list[SkillCandidate], str]:
    repo, url_ref, subpath = parse_github_url(url)
    target_ref = (ref or url_ref).strip()
    subpath = (path or subpath).strip().strip("/")
    info = resolve_repo(repo, target_ref)
    tree = GithubTree(info)
    candidates = tree.find_candidates()
    if not candidates:
        raise SkillInstallError("该仓库没有找到 SKILL.md，不是有效的技能包仓库")
    return info, tree, candidates, subpath


def preview(url: str, ref: str = "", path: str = "") -> dict:
    info, _tree, candidates, subpath = _plan(url, ref, path)

    selected = None
    if subpath:
        hit = next((c for c in candidates if c.path == subpath), None)
        if hit is None:
            raise SkillInstallError(f"目录 {subpath} 下没有 SKILL.md")
        # 地址里已经指定了技能目录：只展示这一个，避免大仓库的候选列表淹没意图
        candidates = [hit]
        selected = hit.to_public()
    elif len(candidates) == 1:
        selected = candidates[0].to_public()

    return {
        "repo": info.to_public(),
        "candidates": [c.to_public() for c in candidates],
        "selected": selected,
    }


def install(
    store: SkillStore,
    workspace_id: str,
    url: str,
    ref: str = "",
    path: str = "",
    owner_id: str = "",
) -> tuple[Skill, bool]:
    """安装或更新技能，返回 (技能, 是否新建)。"""
    info, tree, candidates, subpath = _plan(url, ref, path)

    if subpath:
        candidate = next((c for c in candidates if c.path == subpath), None)
        if candidate is None:
            raise SkillInstallError(f"目录 {subpath} 下没有 SKILL.md")
    elif len(candidates) == 1:
        candidate = candidates[0]
    else:
        raise SkillInstallError(
            f"该仓库包含 {len(candidates)} 个技能，请选择要安装的目录："
            + "、".join(c.path or "." for c in candidates[:6])
        )

    payload = tree.collect(candidate.path)
    skill_md = payload.get("SKILL.md") or payload.get("skill.md")
    if skill_md is None:
        raise SkillInstallError("技能目录缺少 SKILL.md")
    front, body = parse_frontmatter(skill_md.decode("utf-8", errors="replace"))
    if not body.strip():
        raise SkillInstallError("SKILL.md 正文为空，无法作为技能提示词")

    prompt = body.strip()
    if len(prompt) > MAX_PROMPT_CHARS:
        prompt = prompt[:MAX_PROMPT_CHARS] + "\n\n（技能说明较长，已截断；完整内容可在技能文件中查看）"

    skill_id = _skill_id(workspace_id, info.repo, candidate.path, owner_id)
    manifest = _write_files(skill_id, payload)

    name = str(front.get("name") or candidate.name or info.repo).strip()[:80]
    description = str(front.get("description") or info.description or "").strip()[:400]
    now = _now()
    skill = Skill(
        id=skill_id,
        name=name,
        description=description,
        system_prompt=prompt,
        tools=[],
        builtin=False,
        enabled=True,
        workspace_id=workspace_id,
        source="github",
        source_url=(
            f"https://github.com/{info.repo}/tree/{info.ref}/{candidate.path}"
            if candidate.path
            else f"https://github.com/{info.repo}/tree/{info.ref}"
        ),
        repo=info.repo,
        ref=info.ref,
        commit=info.commit,
        subpath=candidate.path,
        author=info.author,
        license=info.license,
        files=manifest,
        installed_at=now,
        updated_at=now,
        owner_id=owner_id,
    )
    created = store.upsert(skill)
    store.save()
    return store.get(skill_id) or skill, created


# ---------- zip 导入导出（本地技能包互通） ----------


def export_zip(skill: Skill) -> bytes:
    """把技能（元数据 + 全部文件）打包为 zip，返回字节流。"""
    import io
    import zipfile

    buf = io.BytesIO()
    root = SKILLS_DIR / skill.id
    meta = {
        "id": skill.id,
        "name": skill.name,
        "description": skill.description,
        "system_prompt": skill.system_prompt,
        "tools": skill.tools,
        "version": skill.version,
        "source": skill.source,
        "source_url": skill.source_url,
        "repo": skill.repo,
        "ref": skill.ref,
        "author": skill.author,
        "license": skill.license,
    }
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("skill.json", json.dumps(meta, ensure_ascii=False, indent=2))
        if root.exists():
            # iter_files 跳过不可访问条目（悬空软链等），打包不再因单个坏文件抛错
            from app.services.capabilities.sandbox import iter_files
            for path, _st in sorted(iter_files(root), key=lambda item: item[0]):
                zf.write(path, path.relative_to(root).as_posix())
    return buf.getvalue()


MAX_IMPORT_BYTES = 50 * 1024 * 1024  # zip 解压后总大小上限（防炸弹）


def import_zip(
    data: bytes, store: SkillStore, workspace_id: str, owner_id: str = "",
) -> Skill:
    """从 zip 导入技能：skill.json 元数据 + 其余文件原样落盘。"""
    import io
    import zipfile

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SkillInstallError("不是有效的 zip 文件") from exc
    try:
        total = sum(info.file_size for info in zf.infolist())
        if total > MAX_IMPORT_BYTES:
            raise SkillInstallError("zip 解压后超过 50MB 上限")
        raw = zf.read("skill.json") if "skill.json" in zf.namelist() else None
        if raw is None:
            raise SkillInstallError("zip 缺少 skill.json 元数据（请先导出再迁移）")
        meta = json.loads(raw.decode("utf-8"))
        skill = store.add(
            name=str(meta.get("name") or "导入技能")[:80],
            description=str(meta.get("description") or "")[:400],
            system_prompt=str(meta.get("system_prompt") or "")[:MAX_PROMPT_CHARS],
            tools=[str(t) for t in meta.get("tools", [])],
            workspace_id=workspace_id,
            owner_id=owner_id,
        )
        skill.source = "zip"
        skill.source_url = str(meta.get("source_url") or "")
        skill.repo = str(meta.get("repo") or "")
        skill.version = str(meta.get("version") or "0.1.0")
        skill.installed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        root = (SKILLS_DIR / skill.id).resolve()
        root.mkdir(parents=True, exist_ok=True)
        manifest: list[dict] = []
        for name in zf.namelist():
            if name == "skill.json" or name.endswith("/"):
                continue
            dest = (root / name).resolve()
            if root not in dest.parents:
                continue  # 路径穿越保护
            dest.parent.mkdir(parents=True, exist_ok=True)
            content = zf.read(name)
            dest.write_bytes(content)
            manifest.append({"path": name, "size": len(content)})
        skill.files = sorted(manifest, key=lambda item: item["path"])
        store.save()
        return skill
    finally:
        zf.close()
