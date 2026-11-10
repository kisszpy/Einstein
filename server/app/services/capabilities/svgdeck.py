"""svgdeck —— ppt-master（75k★，MIT）SVG→原生 DrawingML 管线的平台接入。

与 slidep（WorkBuddy 私有 CLI）并列的第二条原生 PPT 管线：
- 智能体逐页写 1280×720 SVG 到 ``slides/NN.svg``（01.svg 起，纯 SVG 1.1）
- 可选 ``slides/master.svg`` 母板：编译时合成到每页底层，统一背景/页眉/
  装饰，让整份 PPT 有设计规范（母板元素垫底，页面内容自然覆盖其上）
- ``compile`` 一次性把全部页面编译成**原生形状** pptx——AUTO_SHAPE /
  TEXT_BOX / GROUP / 渐变，PowerPoint / WPS 里全部可继续编辑，非位图

转换核心来自 tools/ppt-master（MIT，已随仓库 vendored），路径可用环境变量
PPTMASTER_SCRIPTS 覆盖；缺失时抛 SandboxError，智能体按提示词降级其他路线。
"""

import hashlib
import json
import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from app.core.logging import get_logger
from app.services.capabilities import sandbox

logger = get_logger(__name__)

SVG_NS = "http://www.w3.org/2000/svg"
DSL_MAX_CHARS = 200_000
MASTER_NAME = "master.svg"
# ppt-master 转换器唯一支持的画布（见其 dimensions.CANVAS_FORMATS）
CANVAS = "ppt169"
CANVAS_SIZE = (1280, 720)


def converter_dir() -> Path:
    env = os.environ.get("PPTMASTER_SCRIPTS", "").strip()
    candidates = [Path(env)] if env else []
    repo_root = Path(__file__).resolve().parents[4]  # capabilities -> app -> server -> <repo>
    candidates.append(repo_root / "tools" / "ppt-master" / "skills" / "ppt-master" / "scripts")
    for cand in candidates:
        if (cand / "svg_to_pptx").is_dir():
            return cand
    raise sandbox.SandboxError(
        "svgdeck 转换器不可用：未找到 ppt-master（tools/ppt-master），"
        "或用环境变量 PPTMASTER_SCRIPTS 指定其 scripts 目录"
    )


def _parse_svg(text: str, what: str) -> ET.Element:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise sandbox.SandboxError(f"{what} 不是合法的 SVG：{exc}") from exc
    if not root.tag.endswith("}svg") and root.tag != "svg":
        raise sandbox.SandboxError(f"{what} 根元素必须是 <svg>")
    return root


def compose_master(page: str, master: str) -> str:
    """母板合成：master 的绘制元素包成 <g> 垫到页面底层，defs 提升到根级。

    master 内的 id 统一加 mm_ 前缀并重写其 url(#…)/href 引用，避免与
    页面自身的 id 冲突；母板元素在前（先绘制=在下层），页面内容覆盖其上。
    注意 defs 必须合并到页面根级——转换器（ppt-master）只在根级注册渐变，
    也只对根级背景提升（p:bg）生效，包进 <g> 会导致渐变丢失。
    """
    page_root = _parse_svg(page, "页面 SVG")
    master_root = _parse_svg(master, "母板 SVG")

    # 收集 master 子树所有 id（含 defs 里的渐变/滤镜），统一改前缀
    href_attrs = ("href", "{http://www.w3.org/1999/xlink}href")
    rename: dict[str, str] = {}
    for el in master_root.iter():
        old = el.get("id")
        if old:
            rename[old] = f"mm_{old}"
    for el in master_root.iter():
        if el.get("id"):
            el.set("id", rename[el.get("id")])
        for attr in href_attrs:
            val = el.get(attr)
            if val and val.startswith("#") and val[1:] in rename:
                el.set(attr, "#" + rename[val[1:]])
        # fill/stroke/filter 等 url(#id) 引用
        for attr, val in list(el.attrib.items()):
            if "url(#" in val:
                def _sub(m: "re.Match[str]") -> str:
                    return f"url(#{rename.get(m.group(1), m.group(1))}"
                el.set(attr, re.sub(r"url\(#([^)\s]+)", _sub, val))

    # 母板 defs 提升到页面根级（与页面自身 defs 合并）
    SVG_DEF_TAGS = {f"{{{SVG_NS}}}defs", "defs"}
    page_defs = next((c for c in page_root if c.tag in SVG_DEF_TAGS), None)
    master_defs = [c for c in master_root if c.tag in SVG_DEF_TAGS]
    if master_defs:
        if page_defs is None:
            page_defs = ET.Element(f"{{{SVG_NS}}}defs")
            page_root.insert(0, page_defs)
        for mdef in master_defs:
            master_root.remove(mdef)
            for child in list(mdef):
                page_defs.append(child)

    wrapper = ET.SubElement(page_root, f"{{{SVG_NS}}}g")
    wrapper.set("data-layer", "master")
    for child in list(master_root):
        wrapper.append(child)  # moves elements out of master tree
    # 元素顺序决定绘制层级：把母板组挪到最前（先绘制=垫底）。
    # defs 若是刚创建的会一起被挪——defs 位置不影响语义，保持组在最前即可
    page_root.remove(wrapper)
    page_root.insert(0, wrapper)

    ET.register_namespace("", SVG_NS)
    return ET.tostring(page_root, encoding="unicode")


def write_page(root: Path, page_index: int, svg: str, project: str = "") -> str:
    """写一页 SVG 到 slides/NN.svg。page_index<0 表示追加到下一页。

    返回落盘的相对路径（项目内 posix 风格）。非法 XML / 尺寸不符直接报错。
    """
    parsed = _parse_svg(svg, "SVG")
    width, height = _canvas_size(parsed)
    if width != CANVAS_SIZE[0] or height != CANVAS_SIZE[1]:
        raise sandbox.SandboxError(
            f"SVG 画布必须是 {CANVAS_SIZE[0]}×{CANVAS_SIZE[1]}，当前 {width}×{height}"
        )
    svg_dir = root / "slides"
    svg_dir.mkdir(parents=True, exist_ok=True)
    if int(page_index) < 0:
        existing = [p.stem for p in svg_dir.glob("[0-9]" * 2 + ".svg")]
        page_index = max((int(s) for s in existing), default=0) + 1
    idx = int(page_index)
    if not 1 <= idx <= 200:
        raise sandbox.SandboxError("页号必须在 1-200 之间")
    path = svg_dir / f"{idx:02d}.svg"
    path.write_text(svg[:DSL_MAX_CHARS], encoding="utf-8")
    return path.relative_to(root).as_posix()


def _canvas_size(root: ET.Element) -> tuple[float | None, float | None]:
    def _num(val: str | None) -> float | None:
        if not val:
            return None
        m = re.match(r"^-?\d+(?:\.\d+)?", val.strip())
        return float(m.group(0)) if m else None

    vb = root.get("viewBox")
    if vb:
        parts = vb.replace(",", " ").split()
        if len(parts) == 4:
            try:
                return float(parts[2]), float(parts[3])
            except ValueError as exc:
                # viewBox 写得不规范：退回用 width/height 推画布尺寸
                logger.debug("viewBox 解析失败，回退 width/height：%s", exc)
    return _num(root.get("width")), _num(root.get("height"))


def compile_deck(root: Path, rel: str) -> dict:
    """把 slides/*.svg（+可选母板）整体编译成原生形状 pptx 并登记清单。

    **为什么不做「只替换变更页」的增量编译**（2026-11-10 实测结论）：
    整份 5 页编译 0.25s，10 页 0.32s；其中 1.5s 的首次开销全花在 skia / 字体库
    初始化上，与页数无关，进程热了就不再付。而单页替换要动 pptx 内部的
    slideN.xml + rels + media 引用（layout/master 的 rId 也必须对齐），任何一处
    对不上就是「PowerPoint 打不开」——为了 0.05s 去冒这个险不划算。
    真正该省的是「内容没变还重编译」，靠下面的内容指纹判断。
    """
    from app.services.capabilities import deck  # 局部导入避免环

    target = sandbox.resolve_in(root, rel)
    svg_dir = root / "slides"
    pages = sorted(svg_dir.glob("[0-9]" * 2 + ".svg")) if svg_dir.is_dir() else []
    if not pages:
        raise sandbox.SandboxError("slides/ 下没有页面：先用 fs_write 写 slides/01.svg … 再编译")
    master_path = svg_dir / MASTER_NAME
    master = master_path.read_text(encoding="utf-8") if master_path.is_file() else ""

    page_files = [p.relative_to(root).as_posix() for p in pages]
    fingerprint = _fingerprint(pages, master)
    # 内容、母板、页序都没变：直接复用已有 pptx。
    # 编辑器里反复点保存、来回切页都会走到这里，不判就等于每次都重编译。
    if target.is_file() and _stored_fingerprint(root, rel) == fingerprint:
        logger.debug("deck %s 内容未变，跳过重编译", rel)
        return {
            "ok": True, "path": rel, "pages": len(pages),
            "master": bool(master), "cached": True,
        }

    target.parent.mkdir(parents=True, exist_ok=True)
    # 转换器要求 SVG 源文件位于 resource_root 内；合成结果写到项目内
    # 隐藏临时目录（SKIP_DIRS 命中，不会出现在文件树/产物清单），编译后清理
    tmp_dir = root / ".agent_tmp" / "svgdeck"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        files: list[Path] = []
        for i, page in enumerate(pages, 1):
            text = page.read_text(encoding="utf-8")
            if master:
                text = compose_master(text, master)
            tmp_file = tmp_dir / f"slide{i:02d}.svg"
            tmp_file.write_text(text, encoding="utf-8")
            files.append(tmp_file)
        _run_converter(files, target, root)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    deck.set_dsl_manifest(root, rel, "svgdeck", page_files, source_hash=fingerprint)
    return {"ok": True, "path": rel, "pages": len(pages), "master": bool(master)}


def _fingerprint(pages: list[Path], master: str) -> str:
    """内容指纹：页序、每页原文、母板、画布任一变化都必须重编译。

    只哈希内容不哈希 mtime——编辑器保存同一份内容时 mtime 会变，按 mtime
    判失效会让「点两次保存」白跑一次编译。
    """
    h = hashlib.sha256()
    h.update(f"svgdeck:v1|{CANVAS}|".encode())
    h.update(master.encode("utf-8"))
    for page in pages:
        h.update(f"|{page.name}=".encode())
        h.update(page.read_bytes())
    return h.hexdigest()


def _stored_fingerprint(root: Path, rel: str) -> str:
    """上次编译记录的内容指纹；清单缺失/损坏/是别的引擎一律返回空串（=缓存失效）。"""
    from app.services.capabilities import deck  # 局部导入避免环

    try:
        data = json.loads(deck.sidecar_path(root, rel).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(data, dict) or data.get("_engine") != "svgdeck":
        # 结构化清单（ppt_create 产物）没有指纹字段，不能用它判缓存
        return ""
    return str(data.get("_source_hash") or "")


# 预热用的最小页面：只要能被转换器吃下即可，内容无所谓
_WARMUP_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" '
    'viewBox="0 0 1280 720"><rect width="1280" height="720" fill="#ffffff"/></svg>'
)


def warm_up() -> None:
    """预热转换器，把 1.5s 的冷启动代价挪到服务启动时（后台线程）。

    首次 import skia-pathops / 加载字体要 1.5s 左右，之后同样一份 deck
    只要 0.25s。不预热的话这笔开销由「第一次点开设计稿的用户」承担。
    转换器缺失或预热失败都只记 debug——这是纯优化，不该影响任何功能。
    """
    try:
        converter_dir()
    except sandbox.SandboxError:
        logger.debug("svgdeck 转换器缺失，跳过预热")
        return
    with tempfile.TemporaryDirectory(prefix="svgdeck-warm-") as tmp:
        root = Path(tmp)
        svg = root / "01.svg"
        svg.write_text(_WARMUP_SVG, encoding="utf-8")
        try:
            _run_converter([svg], root / "warm.pptx", root)
        except sandbox.SandboxError as exc:
            logger.debug("svgdeck 预热失败（不影响使用）：%s", exc)


def _run_converter(files: list[Path], out: Path, resource_root: Path) -> None:
    import sys

    scripts = converter_dir()
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    # 防止之前失败的半成品干扰转换器
    try:
        out.unlink()
    except OSError:
        pass  # 清理失败不影响主流程（Windows 上文件常被占用）
    try:
        from svg_to_pptx import create_pptx_with_native_svg
    except ImportError as exc:
        raise sandbox.SandboxError(f"svgdeck 转换器加载失败：{exc}") from exc
    try:
        create_pptx_with_native_svg(
            files,
            out,
            resource_root=resource_root,
            canvas_format=CANVAS,
            pptx_structure="baseline",
            verbose=False,
            transition=None,
            animation=None,
        )
    except Exception as exc:  # noqa: BLE001 — 转换器内部异常统一转平台语义
        raise sandbox.SandboxError(f"SVG 编译失败：{type(exc).__name__}: {exc}") from exc
    if not out.is_file():
        raise sandbox.SandboxError("SVG 编译失败：转换器未产出文件")


def lint_svg(svg: str) -> dict:
    """轻量校验：XML 合法性 + 画布尺寸 + 明显问题。返回 slidep 同形的诊断。"""
    diagnostics: list[dict] = []
    try:
        root = _parse_svg(svg, "SVG")
    except sandbox.SandboxError as exc:
        return {"ok": False, "diagnostics": [
            {"ruleId": "xml", "severity": "error", "message": str(exc)}]}
    width, height = _canvas_size(root)
    if (width, height) != CANVAS_SIZE:
        diagnostics.append({
            "ruleId": "canvas-size", "severity": "error",
            "message": f"画布应为 1280×720，当前 {width}×{height}（width/height 或 viewBox）",
        })
    texts = sum(1 for _el in root.iter() if _el.tag.endswith("}text") or _el.tag == "text")
    if texts == 0:
        diagnostics.append({
            "ruleId": "no-text", "severity": "warning",
            "message": "页面没有任何 <text> 元素，像是空页或整图页",
        })
    return {"ok": not any(d["severity"] == "error" for d in diagnostics),
            "diagnostics": diagnostics}
