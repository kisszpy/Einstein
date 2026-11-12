import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DesignConsole } from "./DesignConsole";
import { DEFAULT_KNOBS } from "../design";
import type { DesignCatalog, Outline, PreviewResult } from "../types";

/**
 * 设计台的契约：旋钮常驻可见、动一下就回写 outline、实测预览来自后端而不是
 * 前端示意图；浏览器不可用时如实降级，不假装实时。
 */

vi.mock("../api", () => ({
  previewDesign: vi.fn(),
}));

import * as api from "../api";

// 后端色板是裸 hex（无 #），fixture 必须照抄，否则色卡渲染的回归测不出来
const catalog: DesignCatalog = {
  families: [
    {
      id: "builtin:midnight",
      key: "midnight",
      name: "午夜蓝",
      tagline: "深蓝夜幕",
      mode: "dark",
      motif: "orbit",
      swatches: ["0B1223", "4F6BF6", "22B8CF", "FFFFFF"],
      chart: ["4F6BF6", "22B8CF"],
    },
    {
      id: "builtin:ivory",
      key: "ivory",
      name: "象牙纸",
      tagline: "衬线学术",
      mode: "light",
      motif: "grid",
      swatches: ["FFFDF8", "B4552A", "2F6B5B", "000000"],
      chart: ["B4552A", "2F6B5B"],
    },
  ],
  knobs: {
    density: ["loose", "normal", "dense"],
    type_scale: { min: 0.88, max: 1.14, step: 0.02, default: 1 },
    radius: { min: 0, max: 28, step: 1, default: 14 },
    whitespace: { min: 0.8, max: 1.25, step: 0.05, default: 1 },
    graphic: ["auto", "flat", "gradient", "outline"],
    seed: { min: 0, max: 9999 },
  },
  engine: { html: true, reason: "" },
};

function makeOutline(): Outline {
  return {
    title: "智能制造三年跃迁",
    subtitle: "",
    audience: "",
    lang: "zh",
    page_count: 2,
    style: { ...DEFAULT_KNOBS, theme_pack_id: "builtin:midnight", preset_theme: "", tone: "" },
    sources_policy: "off",
    version: 1,
    sections: [
      {
        id: "s1",
        title: "开场",
        pages: [
          { id: "p1", title: "封面", kicker: "", layout: "cover", bullets: [], note: "", sources: [], layout_resolved: "" },
        ],
      },
    ],
    sources: [],
  };
}

const previewResult: PreviewResult = {
  available: true,
  total_slides: 8,
  layouts_used: ["cover", "chart"],
  slides: [
    {
      index: 0,
      title: "智能制造三年跃迁",
      layout: "cover",
      chapter: "开场",
      image: "data:image/jpeg;base64,AAAA",
      report: {
        index: 0, layout: "cover", ok: true, fill: 0.12, min_contrast: 7.4,
        min_type: 16, attempts: 0, repairs: [], issues: [],
      },
    },
    {
      index: 4,
      title: "各产线设备数",
      layout: "chart",
      chapter: "成果",
      image: "data:image/jpeg;base64,BBBB",
      report: {
        index: 4, layout: "chart", ok: true, fill: 0.31, min_contrast: 5.1,
        min_type: 19, attempts: 1, repairs: ["收紧字阶"], issues: [],
      },
    },
  ],
  lint: {
    pages: 2, clean_pages: 2, errors: 0, warnings: 0, rules: {},
    repairs: 1, avg_fill: 0.22, min_contrast: 5.1,
  },
  tokens: {
    key: "midnight",
    mode: "dark",
    colors: { bg: "0B1223", accent: "4F6BF6" },
    chart: ["4F6BF6", "22B8CF"],
    knobs: DEFAULT_KNOBS,
  },
};

function setup(
  overrides: { catalog?: DesignCatalog; sessionId?: string | null; outline?: Outline } = {},
) {
  const onChange = vi.fn();
  const utils = render(
    <DesignConsole
      sessionId={overrides.sessionId === undefined ? "ps_1" : overrides.sessionId}
      outline={overrides.outline ?? makeOutline()}
      onChange={onChange}
      catalog={overrides.catalog ?? catalog}
    />,
  );
  return { onChange, ...utils };
}

beforeEach(() => {
  vi.mocked(api.previewDesign).mockReset();
  vi.mocked(api.previewDesign).mockResolvedValue(previewResult);
});

describe("DesignConsole 控件", () => {
  it("旋钮常驻可见，不需要展开任何面板", () => {
    setup({ catalog: { ...catalog, engine: { html: false, reason: "没有 node" } } });
    for (const label of ["信息密度", "图形风格", "构图变体", "强调色", "预览页数"]) {
      expect(screen.getByRole("group", { name: label })).toBeTruthy();
    }
    for (const label of ["字号倍率", "留白倍率", "卡片圆角"]) {
      expect(screen.getByRole("slider", { name: label })).toBeTruthy();
    }
    for (const label of ["母题装饰", "页脚页码"]) {
      expect(screen.getByRole("switch", { name: label })).toBeTruthy();
    }
    expect(screen.getByLabelText("自定义强调色")).toBeTruthy();
    expect(screen.getByRole("button", { name: /随机一版/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /重置/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /复制风格/ })).toBeTruthy();
  });

  it("拖字号滑块立刻回写 outline.style", async () => {
    const { onChange } = setup({ catalog: { ...catalog, engine: { html: false, reason: "" } } });
    const slider = screen.getByRole("slider", { name: "字号倍率" });
    // range 在 jsdom 里不响应键盘步进，直接派发 change（等价于拖动滑块）
    fireEvent.change(slider, { target: { value: "1.02" } });
    expect(onChange).toHaveBeenCalled();
    expect(onChange.mock.calls.at(-1)?.[0].style.type_scale).toBeCloseTo(1.02, 5);
  });

  it("切密度与图形风格各自回写对应字段", async () => {
    const { onChange } = setup({ catalog: { ...catalog, engine: { html: false, reason: "" } } });
    await userEvent.click(within(screen.getByRole("group", { name: "信息密度" })).getByText("紧凑"));
    expect(onChange.mock.calls.at(-1)?.[0].style.density).toBe("dense");
    await userEvent.click(within(screen.getByRole("group", { name: "图形风格" })).getByText("描边"));
    expect(onChange.mock.calls.at(-1)?.[0].style.graphic).toBe("outline");
  });

  it("换家族清掉 preset_theme", async () => {
    const { onChange } = setup({ catalog: { ...catalog, engine: { html: false, reason: "" } } });
    await userEvent.click(screen.getByRole("button", { name: /象牙纸/ }));
    expect(onChange.mock.calls.at(-1)?.[0].style.theme_pack_id).toBe("builtin:ivory");
    expect(onChange.mock.calls.at(-1)?.[0].style.preset_theme).toBe("");
  });

  it("母题开关翻转，强调色可选可跟随", async () => {
    const { onChange } = setup({ catalog: { ...catalog, engine: { html: false, reason: "" } } });
    await userEvent.click(screen.getByRole("switch", { name: "母题装饰" }));
    expect(onChange.mock.calls.at(-1)?.[0].style.motif).toBe(false);
    await userEvent.click(screen.getByLabelText("强调色 #E11D63"));
    expect(onChange.mock.calls.at(-1)?.[0].style.accent).toBe("#E11D63");
  });

  it("默认态重置禁用；随机一版把家族与旋钮一起换掉", async () => {
    const { onChange } = setup({ catalog: { ...catalog, engine: { html: false, reason: "" } } });
    expect(screen.getByRole("button", { name: /重置/ })).toHaveProperty("disabled", true);
    await userEvent.click(screen.getByRole("button", { name: /随机一版/ }));
    const style = onChange.mock.calls.at(-1)?.[0].style;
    expect(["builtin:midnight", "builtin:ivory"]).toContain(style.theme_pack_id);
    expect(style.seed).toBeGreaterThanOrEqual(0);
    expect(style.footer).toBe(true);
  });

  it("构图变体步进不会越界", async () => {
    const { onChange } = setup({ catalog: { ...catalog, engine: { html: false, reason: "" } } });
    expect(screen.getByLabelText("上一个构图变体")).toHaveProperty("disabled", true);
    await userEvent.click(screen.getByLabelText("下一个构图变体"));
    expect(onChange.mock.calls.at(-1)?.[0].style.seed).toBe(1);
  });

  it("家族色卡各自显示自家配色，不被当前强调色刷成一个样", async () => {
    const base = makeOutline();
    const outline = { ...base, style: { ...base.style, accent: "#E11D63" } };
    render(
      <DesignConsole
        sessionId="ps_1"
        outline={outline}
        onChange={vi.fn()}
        catalog={{ ...catalog, engine: { html: false, reason: "" } }}
      />,
    );
    const chips = ["午夜蓝", "象牙纸"].map((name) => screen.getByRole("button", { name: new RegExp(name) }));
    const bases = chips.map((c) => (c.querySelector("span[aria-hidden]") as HTMLElement).style.background);
    // 裸 hex 直接塞进 background 会被浏览器丢弃，色卡全白就没法挑家族了
    expect(bases).toEqual(["rgb(11, 18, 35)", "rgb(255, 253, 248)"]);
    const wedges = chips.map(
      (c) => (c.querySelector("span[aria-hidden] > span") as HTMLElement).style.background,
    );
    // 选中的午夜蓝跟随强调色，未选中的象牙纸仍是自己的赭石
    expect(wedges).toEqual(["rgb(225, 29, 99)", "rgb(180, 85, 42)"]);
  });
});

describe("DesignConsole 实测预览", () => {
  it("挂载后自动实测，缩略图与体检摘要都落到界面", async () => {
    setup();
    await waitFor(() => expect(api.previewDesign).toHaveBeenCalled());
    expect(vi.mocked(api.previewDesign).mock.calls[0]?.[0]).toBe("ps_1");
    await waitFor(() => expect(screen.getByAltText("第 1 页 智能制造三年跃迁")).toBeTruthy());
    expect(screen.getByAltText("第 5 页 各产线设备数")).toBeTruthy();
    expect(screen.getByText("体检 2/2 页通过")).toBeTruthy();
    expect(screen.getByText("抽样 2 / 全 8 页")).toBeTruthy();
  });

  it("点缩略图放大看片，Esc 关闭", async () => {
    setup();
    await waitFor(() => expect(screen.getByAltText("第 5 页 各产线设备数")).toBeTruthy());
    await userEvent.click(screen.getByTitle("放大看第 5 页：各产线设备数"));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("自动修复：收紧字阶")).toBeTruthy();
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("没有会话 id 时不发预览请求", async () => {
    setup({ sessionId: null });
    await new Promise((resolve) => setTimeout(resolve, 150));
    expect(api.previewDesign).not.toHaveBeenCalled();
  });

  it("浏览器不可用时降级为色板，并说清只有密度生效", async () => {
    setup({ catalog: { ...catalog, engine: { html: false, reason: "没有 node" } } });
    await new Promise((resolve) => setTimeout(resolve, 150));
    expect(api.previewDesign).not.toHaveBeenCalled();
    expect(screen.getByText(/只有「信息密度」会生效/)).toBeTruthy();
    expect(screen.getByText("基础排版")).toBeTruthy();
  });

  it("预览失败保留错误条与重试入口", async () => {
    vi.mocked(api.previewDesign).mockRejectedValue(new Error("排版引擎繁忙"));
    setup();
    await waitFor(() => expect(screen.getByText("排版引擎繁忙")).toBeTruthy());
    expect(screen.getByRole("button", { name: "重试" })).toBeTruthy();
  });
});

// 上传模板被后端抽成一支设计家族（kind=template），排在内置风格前面
const templateFamily: DesignCatalog["families"][number] = {
  id: "tp_9a12",
  key: "template",
  kind: "template",
  name: "集团品牌模板",
  tagline: "浅色基调 · 主色 #1E6FD9 · Microsoft YaHei · 4:3",
  mode: "light",
  motif: "glass",
  swatches: ["FFFFFF", "1E6FD9", "E8873C", "F4F6FA"],
  chart: ["1E6FD9", "E8873C", "2FA36B", "8B5CF6", "D64550", "3FA9C9", "7A8699"],
  ratio: "4:3",
  notes: ["模板字体「阿里巴巴普惠体 3.0」本机未安装，已替换为 Microsoft YaHei"],
  fonts: { display: "Georgia", body: "Microsoft YaHei", source_display: "Georgia", source_body: "阿里巴巴普惠体 3.0" },
  radius: 8,
  margin_px: 56,
};

const withTemplate: DesignCatalog = {
  ...catalog,
  families: [templateFamily, ...catalog.families],
  engine: { html: false, reason: "没有 node" },
};

function templateOutline(): Outline {
  const base = makeOutline();
  return { ...base, style: { ...base.style, theme_pack_id: "tp_9a12" } };
}

describe("DesignConsole 公司模板", () => {
  it("模板家族排最前并带画布比例，选中时用量出来的圆角当起点", async () => {
    const { onChange } = setup({ catalog: withTemplate });
    const chip = screen.getByRole("button", { name: /集团品牌模板/ });
    expect(chip.textContent).toContain("4:3");
    const buttons = screen.getAllByRole("button", { pressed: false });
    expect(buttons[0]).toBe(chip);
    await userEvent.click(chip);
    const style = onChange.mock.calls.at(-1)?.[0].style;
    expect(style.theme_pack_id).toBe("tp_9a12");
    expect(style.preset_theme).toBe("");
    expect(style.radius).toBe(8);
  });

  it("选中模板后字体、版心与适配说明都摊开可见", () => {
    setup({ catalog: withTemplate, outline: templateOutline() });
    expect(screen.getByText("Microsoft YaHei")).toBeTruthy();
    expect(screen.getByText("标题 Georgia")).toBeTruthy();
    expect(screen.getByText("画布 4:3")).toBeTruthy();
    expect(screen.getByText("版心边距 56px")).toBeTruthy();
    expect(screen.getByText("1 处适配")).toBeTruthy();
    expect(screen.getByText(/阿里巴巴普惠体 3.0」本机未安装/)).toBeTruthy();
  });

  it("未选中模板时不显示它的适配说明，避免误读成当前风格", () => {
    setup({ catalog: withTemplate });
    expect(screen.queryByText("1 处适配")).toBeNull();
    expect(screen.getByRole("button", { name: /午夜蓝/ })).toHaveProperty("ariaPressed", "true");
  });

  it("空风格默认落到内置家族，不会悄悄用上目录里的第一个模板", () => {
    const base = makeOutline();
    setup({ catalog: withTemplate, outline: { ...base, style: { ...base.style, theme_pack_id: "" } } });
    expect(screen.getByRole("button", { name: /午夜蓝/ })).toHaveProperty("ariaPressed", "true");
    expect(screen.getByRole("button", { name: /集团品牌模板/ })).toHaveProperty("ariaPressed", "false");
  });

  it("随机一版换排布但不摇走用户选的公司模板", async () => {
    const { onChange } = setup({ catalog: withTemplate, outline: templateOutline() });
    await userEvent.click(screen.getByRole("button", { name: /随机一版/ }));
    const style = onChange.mock.calls.at(-1)?.[0].style;
    expect(style.theme_pack_id).toBe("tp_9a12");
    expect(style.seed).toBeGreaterThanOrEqual(0);
  });

  it("模板同样能拧旋钮：改字号照样回写", () => {
    const { onChange } = setup({ catalog: withTemplate, outline: templateOutline() });
    fireEvent.change(screen.getByRole("slider", { name: "字号倍率" }), { target: { value: "1.06" } });
    const style = onChange.mock.calls.at(-1)?.[0].style;
    expect(style.type_scale).toBeCloseTo(1.06, 5);
    expect(style.theme_pack_id).toBe("tp_9a12");
  });

  it("浏览器不可用时说清模板回落母版套版，而不是只有密度生效", () => {
    setup({ catalog: withTemplate, outline: templateOutline() });
    expect(screen.getByText(/成稿回落到模板母版套版/)).toBeTruthy();
    expect(screen.queryByText(/只有「信息密度」会生效/)).toBeNull();
  });
});