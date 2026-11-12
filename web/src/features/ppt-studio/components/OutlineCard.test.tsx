import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { countContentPages, estimateTotalSlides, OutlineCard } from "./OutlineCard";
import { DEFAULT_KNOBS } from "../design";
import type { Outline } from "../types";

/**
 * 确认卡是「生成前最后一道人审」。锁定四个契约：
 * 页数口径（内容页/附录）、加删页联动、空标题禁生成、>8 要点只提示不阻断。
 */

function makeOutline(overrides: Partial<Outline> = {}): Outline {
  return {
    title: "季度经营复盘",
    subtitle: "",
    audience: "",
    lang: "zh",
    page_count: 3,
    style: { ...DEFAULT_KNOBS, theme_pack_id: "", preset_theme: "", tone: "" },
    sources_policy: "off",
    version: 1,
    sections: [
      {
        id: "sec_1",
        title: "业绩回顾",
        pages: [
          { id: "pg_1", title: "开场", kicker: "", layout: "cover", bullets: [], note: "", sources: [], layout_resolved: "" },
          { id: "pg_2", title: "核心指标", kicker: "", layout: "bullets", bullets: ["营收 +12%"], note: "", sources: [], layout_resolved: "" },
        ],
      },
      {
        id: "sec_2",
        title: "下一步",
        pages: [
          { id: "pg_3", title: "收尾", kicker: "", layout: "end", bullets: [], note: "", sources: [], layout_resolved: "" },
        ],
      },
    ],
    sources: [],
    ...overrides,
  };
}

const base = {
  outline: makeOutline(),
  onChange: () => {},
  onConfirm: () => {},
};

describe("大纲口径", () => {
  it("内容页数 = 各章节页数之和，附录不计入", () => {
    expect(countContentPages(makeOutline())).toBe(3);
  });

  it("policy 为 appendix 且有来源时总页数 = 内容页 + 1", () => {
    const outline = makeOutline({
      sources_policy: "appendix",
      sources: [{ id: "s1", kind: "web", title: "T", url: "", kb_name: "", snippet: "" }],
    });
    expect(estimateTotalSlides(outline)).toBe(4);
  });

  it("policy 为 off 时附录不计入总页数", () => {
    const outline = makeOutline({
      sources_policy: "off",
      sources: [{ id: "s1", kind: "web", title: "T", url: "", kb_name: "", snippet: "" }],
    });
    expect(estimateTotalSlides(outline)).toBe(3);
  });
});

describe("OutlineCard 交互", () => {
  it("空标题禁用「确认并生成」并给出提示，填了标题即恢复", async () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <OutlineCard {...base} onChange={onChange} outline={makeOutline({ title: "" })} />,
    );
    const button = screen.getByRole("button", { name: /确认并生成/ });
    expect(button).toBeDisabled();
    expect(screen.getByText(/填写标题后才能生成/)).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("演示标题"), "新标题");
    expect(onChange).toHaveBeenCalled();

    rerender(<OutlineCard {...base} outline={makeOutline()} />);
    expect(screen.getByRole("button", { name: /确认并生成/ })).toBeEnabled();
  });

  it("加页：点击章节的加页按钮后大纲多一页且新页可命名", async () => {
    const onChange = vi.fn();
    render(<OutlineCard {...base} onChange={onChange} />);
    await userEvent.click(screen.getAllByTitle("在这一章末尾加一页")[0]!);
    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0]![0] as Outline;
    const firstSection = next.sections[0]!;
    expect(firstSection.pages).toHaveLength(3);
    expect(firstSection.pages[2]?.title).toBe("新页面");
    expect(countContentPages(next)).toBe(4);
  });

  it("删页：删除后大纲少一页", async () => {
    const onChange = vi.fn();
    render(<OutlineCard {...base} onChange={onChange} />);
    const delButtons = screen.getAllByTitle("删除这一页");
    await userEvent.click(delButtons[1]!);
    const next = onChange.mock.calls[0]![0] as Outline;
    expect(countContentPages(next)).toBe(2);
    expect(next.sections[0]?.pages.map((p) => p.title)).toEqual(["开场"]);
  });

  it("单页 >8 条要点展示提示但不禁用生成", () => {
    const outline = makeOutline({
      sections: [
        {
          id: "sec_1",
          title: "业绩回顾",
          pages: [
            {
              id: "pg_1",
              title: "大页",
              kicker: "",
              layout: "bullets",
              bullets: Array.from({ length: 9 }, (_, i) => `要点${i + 1}`),
              note: "",
              sources: [],
              layout_resolved: "",
            },
          ],
        },
      ],
    });
    render(<OutlineCard {...base} outline={outline} />);
    expect(screen.getByText(/超出建议上限（8 条）/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /确认并生成/ })).toBeEnabled();
  });

  it("上传的公司模板同样出设计台——模板的风格已抽成一支家族，旋钮照样能拧", () => {
    const outline = makeOutline();
    render(
      <OutlineCard
        {...base}
        outline={{ ...outline, style: { ...outline.style, theme_pack_id: "tp_9a12" } }}
        sessionId="ps_1"
      />,
    );
    expect(screen.getByRole("region", { name: "设计台" })).toBeInTheDocument();
    expect(screen.getByRole("slider", { name: "卡片圆角" })).toBeInTheDocument();
  });
});
