import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ResultCard } from "./ResultCard";
import type { PptGeneration } from "../types";

/**
 * 结果卡的缩略图走票据鉴权：<img> 带不了 Authorization，票据必须拼进 URL；
 * 项目被删（没有 deck）时不能拼出一串必然 401 的地址。
 */

function generation(overrides: Partial<PptGeneration> = {}): PptGeneration {
  return {
    generation_id: "gen_abc123",
    project: "demo",
    deck_path: "deck/季度复盘.pptx",
    total_slides: 8,
    layouts_used: ["cover", "chart", "cards", "bullets", "stat", "quote", "timeline", "end"],
    fidelity: { score: 98, issues: [], degradations: [], summary_zh: "逐页体检全部通过", engine: "html" },
    status: "ok",
    error: "",
    created_at: "2026-11-12T10:00:00Z",
    thumb_count: 8,
    deck: {
      project: "demo",
      path: "deck/季度复盘.pptx",
      name: "季度复盘.pptx",
      bytes: 240_000,
      ticket: "tk_9z",
      download_url: "/api/projects/demo/download?path=deck/季度复盘.pptx&ticket=tk_9z",
    },
    ...overrides,
  };
}

describe("ResultCard 缩略图", () => {
  it("默认只展示前 6 页，地址带票据", () => {
    render(<ResultCard generation={generation()} onOpenEditor={() => {}} />);
    const images = screen.getAllByRole("img");
    expect(images).toHaveLength(6);
    expect(images[0]?.getAttribute("src")).toBe("/api/ppt/thumbs/gen_abc123/0?ticket=tk_9z");
    expect(screen.getByText(/实测排版/)).toBeTruthy();
  });

  it("展开全部后 8 页都在，再点收起回到 6 页", async () => {
    render(<ResultCard generation={generation()} onOpenEditor={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: /展开全部 8 页/ }));
    expect(screen.getAllByRole("img")).toHaveLength(8);
    await userEvent.click(screen.getByRole("button", { name: /收起/ }));
    expect(screen.getAllByRole("img")).toHaveLength(6);
  });

  it("项目已删（无 deck）时不渲染缩略图，只留文字摘要", () => {
    render(<ResultCard generation={generation({ deck: undefined })} onOpenEditor={() => {}} />);
    expect(screen.queryAllByRole("img")).toHaveLength(0);
    expect(screen.getByText("季度复盘.pptx")).toBeTruthy();
  });

  it("回落基础排版没有缩略图，也不显示实测排版标记", () => {
    render(
      <ResultCard
        generation={generation({
          thumb_count: 0,
          fidelity: { score: 88, issues: [], degradations: ["已回落基础排版"], summary_zh: "基础排版", engine: "shapes" },
        })}
        onOpenEditor={() => {}}
      />,
    );
    expect(screen.queryAllByRole("img")).toHaveLength(0);
    expect(screen.queryByText(/实测排版/)).toBeNull();
  });

  it("按上传模板重绘：出处与字体替换不用展开徽章就能看到", () => {
    render(
      <ResultCard
        generation={generation({
          fidelity: {
            score: 94,
            issues: [],
            degradations: ["模板字体「阿里巴巴普惠体 3.0」本机未安装，已替换为 Noto Serif SC"],
            summary_zh: "上传模板「B-4x3-深色」；浏览器实测排版，9 页原生可编辑形状",
            engine: "template",
            template: { name: "B-4x3-深色", ratio: "4:3", mode: "dark" },
          },
        })}
        onOpenEditor={() => {}}
      />,
    );
    // 「像不像我的模板」全靠这两行，藏在保真度徽章后面等于没说
    expect(screen.getByText(/按「B-4x3-深色」风格重绘/)).toBeTruthy();
    expect(screen.getByText(/深色 · 4:3/)).toBeTruthy();
    expect(screen.getByText(/阿里巴巴普惠体 3.0/)).toBeTruthy();
    expect(screen.getByText(/实测排版/)).toBeTruthy();
    // 降级项只出现一次：展开明细里不该再重复一遍
    expect(screen.getAllByText(/阿里巴巴普惠体 3.0/)).toHaveLength(1);
  });

  it("母版套版回落说套版，不谎称重绘", () => {
    render(
      <ResultCard
        generation={generation({
          thumb_count: 0,
          fidelity: {
            score: 84,
            issues: [],
            degradations: ["未启用浏览器实测排版（没有 node），已回落模板母版套版"],
            summary_zh: "11 个版式完整解析",
            engine: "template-shapes",
            template: { name: "公司模板", ratio: "4:3", mode: "light" },
          },
        })}
        onOpenEditor={() => {}}
      />,
    );
    expect(screen.getByText(/按「公司模板」母版套版/)).toBeTruthy();
    expect(screen.queryByText(/· 实测排版/)).toBeNull();
  });

  it("点缩略图能放大看片并关闭", async () => {
    render(<ResultCard generation={generation()} onOpenEditor={() => {}} />);
    await userEvent.click(screen.getByTitle("放大看第 3 页：第 3 页"));
    const dialog = await screen.findByRole("dialog");
    expect(dialog.getAttribute("aria-label")).toContain("第 3 页");
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});