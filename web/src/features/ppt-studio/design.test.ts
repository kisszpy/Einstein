import { describe, expect, it } from "vitest";

import {
  ACCENT_PRESETS,
  DEFAULT_KNOBS,
  FALLBACK_RANGES,
  changedKnobCount,
  cssHex,
  familySeed,
  normalizeKnobs,
  randomizeKnobs,
  withFamily,
  withKnobs,
} from "./design";
import type { DesignFamily, Outline } from "./types";

/**
 * 旋钮的取值域必须前后端一致：前端显示了 1.3× 而后端 clamp 到 1.14×，
 * 用户会以为「拖了没反应」。随机一版也绝不能摇出界外的值。
 */

function outline(): Outline {
  return {
    title: "季度经营复盘",
    subtitle: "",
    audience: "",
    lang: "zh",
    page_count: 3,
    style: { ...DEFAULT_KNOBS, theme_pack_id: "builtin:midnight", preset_theme: "", tone: "" },
    sources_policy: "off",
    version: 1,
    sections: [],
    sources: [],
  };
}

/** 上传模板抽出来的家族卡：带 kind/ratio/notes/radius，和内置家族同形。 */
function templateFamily(): DesignFamily {
  return {
    id: "tp_9a12",
    key: "template",
    kind: "template",
    name: "公司模板",
    tagline: "浅色基调 · 主色 #1E6FD9 · Microsoft YaHei · 4:3",
    mode: "light",
    motif: "glass",
    swatches: ["FFFFFF", "1E6FD9", "E8873C", "F4F6FA"],
    chart: ["1E6FD9", "E8873C", "2FA36B", "8B5CF6", "D64550", "3FA9C9", "7A8699"],
    ratio: "4:3",
    notes: [],
    radius: 8,
    margin_px: 56,
  };
}

/** 确定性 RNG，让「随机」在测试里可复现。 */
function lcg(seed: number) {
  let state = seed;
  return () => {
    state = (state * 1664525 + 1013904223) % 4294967296;
    return state / 4294967296;
  };
}

describe("normalizeKnobs", () => {
  it("越界值 clamp 到取值域，非法枚举回落默认", () => {
    const knobs = normalizeKnobs({
      type_scale: 9,
      radius: -5,
      whitespace: 0.1,
      seed: 1e9,
      density: "wild" as never,
      graphic: "nope" as never,
    });
    expect(knobs.type_scale).toBe(FALLBACK_RANGES.type_scale.max);
    expect(knobs.radius).toBe(0);
    expect(knobs.whitespace).toBe(FALLBACK_RANGES.whitespace.min);
    expect(knobs.seed).toBe(FALLBACK_RANGES.seed.max);
    expect(knobs.density).toBe("normal");
    expect(knobs.graphic).toBe("auto");
  });

  it("强调色只认 #RRGGBB，其余一律跟随家族", () => {
    expect(normalizeKnobs({ accent: "#e11d63" }).accent).toBe("#E11D63");
    expect(normalizeKnobs({ accent: "red" }).accent).toBe("");
    expect(normalizeKnobs({ accent: "#fff" }).accent).toBe("");
  });

  it("装饰与页脚默认开，显式 false 才关", () => {
    expect(normalizeKnobs(undefined).motif).toBe(true);
    expect(normalizeKnobs({}).footer).toBe(true);
    expect(normalizeKnobs({ motif: false, footer: false }).motif).toBe(false);
    expect(normalizeKnobs({ motif: false, footer: false }).footer).toBe(false);
  });

  it("缺字段时补默认，不产出 undefined", () => {
    expect(normalizeKnobs({})).toEqual(DEFAULT_KNOBS);
  });
});

describe("outline 改写", () => {
  it("withKnobs 只动 style，且不修改原对象", () => {
    const base = outline();
    const next = withKnobs(base, { radius: 24 });
    expect(next.style.radius).toBe(24);
    expect(next.style.theme_pack_id).toBe("builtin:midnight");
    expect(base.style.radius).toBe(DEFAULT_KNOBS.radius);
    expect(next.sections).toBe(base.sections);
  });

  it("换家族会清掉 preset_theme，避免两条风格来源打架", () => {
    const base = { ...outline(), style: { ...outline().style, preset_theme: "ocean" } };
    expect(withFamily(base, "builtin:ivory").style).toMatchObject({
      theme_pack_id: "builtin:ivory",
      preset_theme: "",
    });
  });

  it("换家族可以带上旋钮起点，模板量出的圆角直接生效", () => {
    const next = withFamily(outline(), "tp_9a12", familySeed(templateFamily()));
    expect(next.style.theme_pack_id).toBe("tp_9a12");
    expect(next.style.radius).toBe(8);
  });

  it("familySeed 只对模板生效，且越界圆角 clamp 回取值域", () => {
    expect(familySeed(undefined)).toEqual({});
    expect(
      familySeed({ ...templateFamily(), kind: "builtin", radius: 20 } as DesignFamily),
    ).toEqual({});
    expect(familySeed({ ...templateFamily(), radius: 999 })).toEqual({ radius: 28 });
    expect(familySeed({ ...templateFamily(), radius: -3 })).toEqual({ radius: 0 });
  });
});

describe("randomizeKnobs", () => {
  it("两百次抽样全部落在取值域内，且不动页脚开关", () => {
    const families = ["builtin:midnight", "builtin:ivory", "builtin:sunset"];
    const random = lcg(20261112);
    for (let i = 0; i < 200; i += 1) {
      const { knobs, familyId } = randomizeKnobs(
        { ...DEFAULT_KNOBS, footer: false },
        families,
        FALLBACK_RANGES,
        random,
      );
      expect(knobs.type_scale).toBeGreaterThanOrEqual(FALLBACK_RANGES.type_scale.min);
      expect(knobs.type_scale).toBeLessThanOrEqual(FALLBACK_RANGES.type_scale.max);
      expect(knobs.radius).toBeGreaterThanOrEqual(0);
      expect(knobs.radius).toBeLessThanOrEqual(28);
      expect(knobs.whitespace).toBeGreaterThanOrEqual(0.8);
      expect(knobs.whitespace).toBeLessThanOrEqual(1.25);
      expect(knobs.seed).toBeGreaterThanOrEqual(0);
      expect(knobs.seed).toBeLessThanOrEqual(9999);
      expect(FALLBACK_RANGES.density).toContain(knobs.density);
      expect(FALLBACK_RANGES.graphic).toContain(knobs.graphic);
      expect(families).toContain(familyId);
      expect(knobs.footer).toBe(false);
      if (knobs.accent) expect(ACCENT_PRESETS).toContain(knobs.accent);
      // 摇出来的值必须能通过 normalize 而不被改写，否则显示与成稿会不一致
      expect(normalizeKnobs(knobs)).toEqual(knobs);
    }
  });

  it("没有家族目录时也给出可用的默认家族", () => {
    expect(randomizeKnobs(DEFAULT_KNOBS, [], FALLBACK_RANGES, lcg(1)).familyId).toBe(
      "builtin:midnight",
    );
  });
});

describe("changedKnobCount", () => {
  it("默认值 0 项，改两项就是 2", () => {
    expect(changedKnobCount(DEFAULT_KNOBS)).toBe(0);
    expect(changedKnobCount({ ...DEFAULT_KNOBS, radius: 0, accent: "#E11D63" })).toBe(2);
  });
});
describe("cssHex", () => {
  it("给后端裸 hex 补 #，已有 # 与其它 CSS 颜色原样透传", () => {
    expect(cssHex("0B1223")).toBe("#0B1223");
    expect(cssHex("#0b1223")).toBe("#0b1223");
    expect(cssHex("fff")).toBe("#fff");
    expect(cssHex("rgba(0,0,0,.5)")).toBe("rgba(0,0,0,.5)");
    expect(cssHex("")).toBe("#000000");
    expect(cssHex(undefined, "#4F6BF6")).toBe("#4F6BF6");
  });
});
