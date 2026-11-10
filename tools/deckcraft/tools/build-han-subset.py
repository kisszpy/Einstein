#!/usr/bin/env python3
"""Rebuild the web build's Simplified Chinese faces (`fonts/han-sans-sc/`).

Upstream craft-fonts ships Noto Sans CJK SC as a 16.4 MB OTF and reads Chinese manifests on
native builds only. Embedding that file in the wasm would add ~16 MB per weight, so this script
subsets it to the characters a Chinese deck actually uses and renames the family to
`DeckCraft Han Sans` (a Modified Version under OFL-1.1; the fork's asset policy also excludes the
Noto CJK name).

Usage, from the repo root:

    python tools/build-han-subset.py --craft-fonts ../craft-fonts

It rewrites `fonts/han-sans-sc/*.otf` and prints the SHA-256 of each file, which the rows in
`fonts/cjk-manifest.txt` record. Then rebuild the web app:

    CRAFT_FONTS_DIR=<absolute path to craft-fonts> trunk build --release   # in apps/deckcraft-web

Coverage: GBK (21,003 ideographs — the full set a Chinese IME can type) plus Latin-1, General
Punctuation, arrows, circled numbers, geometric shapes, CJK punctuation and full-width forms —
21,766 code points. GB2312's 6,763 characters and the 8,105-character 通用规范汉字表 are both
subsets, so personal-name characters such as 龘/珺/玥 are covered; CJK extension A/B (𬭚 and
friends) is not, and renders as .notdef. To widen it further, add the code points to
`code_points()` and rerun; each weight costs roughly 0.35 KB per glyph.
"""

from __future__ import annotations

import argparse
import hashlib
import urllib.request
from pathlib import Path

FAMILY = "DeckCraft Han Sans"
STYLES = ("Regular", "Bold")
# The commit fonts/manifest.txt pins for Noto Sans CJK SC.
NOTO_PIN = "f8d157532fbfaeda587e826d4cd5b21a49186f7c"
NOTO_URL = (
    "https://raw.githubusercontent.com/notofonts/noto-cjk/{pin}/Sans/OTF/SimplifiedChinese/"
    "NotoSansCJKsc-{style}.otf"
)


def code_points() -> str:
    """The characters the subset keeps, as a string."""
    # GBK 全集（GB18030 双字节区）：21,003 个汉字 + 双字节符号。
    # 为什么不是 GB2312：GB2312 只有 6,763 字，人名用字（龘/珺/玥/犇/喆/珉…）
    # 全部落在 GBK 扩展区，缺字就画成方框。GBK 才是「中文输入法能打出来」的边界，
    # 也是产品口径：凡是用户能打出来的字，演示里就一定能显示。
    # 通用规范汉字表 8,105 字是 GBK 的子集，无需单独并集。
    gbk: set[str] = set()
    for high in range(0x81, 0xFF):
        for low in range(0x40, 0xFF):
            try:
                gbk.add(bytes([high, low]).decode("gb18030"))
            except UnicodeDecodeError:
                continue
    ranges = [
        (0x20, 0x7F),    # ASCII
        (0xA0, 0x100),   # Latin-1 Supplement
        (0x2000, 0x2070),  # General Punctuation, super/subscripts, currency
        (0x2190, 0x2200),  # arrows
        (0x2460, 0x2500),  # circled numbers, box drawing
        (0x25A0, 0x2600),  # geometric shapes
        (0x3000, 0x3040),  # CJK punctuation
        (0xFF00, 0xFFF0),  # full-width forms
    ]
    extra = {chr(c) for start, end in ranges for c in range(start, end)}
    return "".join(sorted(gbk | extra))


def source_face(noto_dir: Path, style: str) -> Path:
    """The full Noto OTF for `style`, downloading the Bold (absent from craft-fonts) if needed."""
    path = noto_dir / f"NotoSansCJKsc-{style}.otf"
    if path.exists():
        return path
    url = NOTO_URL.format(pin=NOTO_PIN, style=style)
    print(f"downloading {url}")
    with urllib.request.urlopen(url) as response, path.open("wb") as out:
        out.write(response.read())
    return path


def rename(path: Path, style: str) -> None:
    """Rewrite the name table so the family is ours and upstream's name never appears in the UI."""
    from fontTools.ttLib import TTFont

    font = TTFont(path)
    names = {
        1: FAMILY,                                    # family
        2: style,                                     # subfamily
        3: f"{FAMILY} {style}; deckcraft-fork",       # unique id
        4: f"{FAMILY} {style}",                       # full name
        6: f"DeckCraftHanSans-{style}",               # PostScript name
        16: FAMILY,                                   # typographic family
        17: style,                                    # typographic subfamily
        18: f"{FAMILY} {style}",                      # compatible full
        21: FAMILY,                                   # WWS family
        22: style,                                    # WWS subfamily
    }
    for record in list(font["name"].names):
        value = names.get(record.nameID)
        if value is None:
            continue
        try:
            font["name"].setName(value, record.nameID, record.platformID, record.platEncID, record.langID)
        except Exception:  # a platform/encoding pair that rejects the string keeps its old value
            pass
    font.save(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--craft-fonts",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "craft-fonts",
        help="craft-fonts checkout (default: tools/craft-fonts next to this fork)",
    )
    args = parser.parse_args()

    noto_dir = args.craft_fonts / "fonts" / "noto-sans-cjk-sc"
    out_dir = args.craft_fonts / "fonts" / "han-sans-sc"
    out_dir.mkdir(parents=True, exist_ok=True)

    from fontTools import subset

    chars = code_points()
    print(f"subset charset: {len(chars)} code points")
    for style in STYLES:
        src = source_face(noto_dir, style)
        out = out_dir / f"DeckCraftHanSans-{style}.otf"
        subset.main([
            str(src),
            f"--text={chars}",
            f"--output-file={out}",
            "--layout-features=*",   # keep GSUB/GPOS so kerning and vertical forms survive
            "--drop-tables+=DSIG",
            "--name-IDs=*",
            "--notdef-outline",
            "--no-hinting",
        ])
        rename(out, style)
        digest = hashlib.sha256(out.read_bytes()).hexdigest()
        print(f"{out.name:32s} {out.stat().st_size / 1048576:5.2f} MB  sha256={digest}")

    licence = noto_dir / "OFL.txt"
    if licence.exists():
        (out_dir / "OFL.txt").write_bytes(licence.read_bytes())
    print("\nupdate the sha256 values in fonts/cjk-manifest.txt, then:")
    print("  CRAFT_FONTS_DIR=<abs path to craft-fonts> trunk build --release   # in apps/deckcraft-web")


if __name__ == "__main__":
    main()
