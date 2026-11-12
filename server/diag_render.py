"""把一份大纲逐页渲染成 1280 全尺寸 PNG，用来肉眼查版式硬伤。

用法：python diag_render.py <outline.json> <out_dir> [theme_pack_id] [type_scale]
"""
from __future__ import annotations

import io
import json
import shutil
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.services.ppt_studio import designs  # noqa: E402
from app.services.ppt_studio.htmlkit import compose, lint, measure, tokens as tk  # noqa: E402
from app.services.ppt_studio.outline import Outline  # noqa: E402


def main() -> None:
    outline_path, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    raw = json.loads(outline_path.read_text(encoding="utf-8"))
    if len(sys.argv) > 3:
        raw["style"]["theme_pack_id"] = sys.argv[3]
    if len(sys.argv) > 4:
        raw["style"]["type_scale"] = float(sys.argv[4])
    outline = Outline.model_validate(raw)
    preset = designs.resolve(outline.style.theme_pack_id)
    knobs = tk.Knobs.from_style(outline.style)
    tokens = tk.resolve_tokens(preset, knobs)
    plan = compose.plan_deck(outline, tokens)
    slides = compose.compose_deck(outline, tokens, plan)
    pages = outline.flatten_pages()
    out_dir.mkdir(parents=True, exist_ok=True)
    canvas = (tokens["canvas_w"], tokens["canvas_h"])
    measured = measure.measure(slides, out_dir / "pass-0", screenshot=True, canvas=canvas)
    by_index = {int(m["index"]): m for m in measured}
    reports = {i: lint.inspect(m, tokens, layout=slides[i].kind) for i, m in by_index.items()}

    # 和 renderer.render 走同一条修复阶梯，否则看到的是修复前的毛坯
    overrides: dict[int, compose.PageOverride] = {}
    for round_no in range(1, 4):
        bad = [i for i in sorted(reports) if not reports[i].ok]
        if not bad:
            break
        touched: list[int] = []
        for index in bad:
            report = reports[index]
            page = pages[index] if index < len(pages) else None
            has_cards = bool(page and any(len(b) > 24 for b in page.bullets))
            repair = lint.next_repair(report, has_cards=has_cards)
            if repair is None:
                continue
            previous = overrides.get(index) or compose.PageOverride()
            overrides[index] = compose.PageOverride(
                layout=repair.layout or previous.layout,
                scale=min(previous.scale, repair.scale),
                drop=previous.drop + repair.drop,
            )
            report.repairs.append(repair.label())
            touched.append(index)
        if not touched:
            break
        rebuilt = compose.compose_deck(outline, tokens, plan, overrides)
        fixed = measure.measure([rebuilt[i] for i in touched], out_dir / f"pass-{round_no}", screenshot=True, canvas=canvas)
        for item in fixed:
            index = int(item["index"])
            by_index[index] = item
            slides[index] = rebuilt[index]
            fresh = lint.inspect(item, tokens, layout=slides[index].kind)
            fresh.attempts = round_no
            fresh.repairs = reports[index].repairs
            reports[index] = fresh

    for idx in sorted(by_index):
        item, report = by_index[idx], reports[idx]
        shot = item.get("screenshot")
        if shot and Path(shot).is_file():
            shutil.copy(shot, out_dir / f"page-{idx:02d}-{slides[idx].kind}.png")
        print(f"[{idx:02d}] {slides[idx].kind:9s} fill={report.fill:.2f} "
              f"contrast={report.min_contrast:.1f} minType={report.min_type:.0f} "
              f"repairs={report.repairs} issues={[i.rule for i in report.issues]}")
        for issue in report.issues:
            print(f"      - {issue.rule}: {issue.detail}")
    print("out:", out_dir)


main()