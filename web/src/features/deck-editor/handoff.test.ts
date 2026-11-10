import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { stageHandoff, takeHandoff } from "./handoff";

/**
 * 「整页打开」用 sessionStorage 交接演示地址：地址栏保持干净的 `/deckcraft`，
 * 不再出现 `?deck=%2Fapi%2Fprojects%2F…%2Fdownload%3Fpath%3D…`。
 *
 * 新窗口（同源 window.open）会复制 opener 的 sessionStorage，所以新页读得到；
 * 有效期 60 秒，用于挡住「新窗口被拦截后残留，之后从侧栏进来误用上次文件」。
 */
describe("整页打开的跨窗口交接", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.useRealTimers();
  });

  afterEach(() => {
    sessionStorage.clear();
    vi.useRealTimers();
  });

  it("新窗口能取到地址，且只消费一次", () => {
    const payload = { url: "/api/projects/demo/download?path=a.pptx&ticket=t", name: "a.pptx" };
    expect(stageHandoff(payload)).toBe(true);

    expect(takeHandoff()).toEqual(payload);
    expect(takeHandoff()).toBeNull();
  });

  it("不依赖 window.opener（COOP 下 opener 为 null 也要能用）", () => {
    Object.defineProperty(window, "opener", { value: null, configurable: true, writable: true });
    stageHandoff({ url: "/samples/deck.pptx" });

    expect(takeHandoff()?.url).toBe("/samples/deck.pptx");
  });

  it("超过 60 秒的残留交接作废", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-11T01:00:00Z"));
    stageHandoff({ url: "/samples/deck.pptx", name: "deck.pptx" });

    vi.setSystemTime(new Date("2026-11-11T01:02:00Z"));
    expect(takeHandoff()).toBeNull();
    // 取过就删，不给下一次误用
    expect(sessionStorage.getItem("deckcraft:handoff")).toBeNull();
  });

  it("内容损坏时当作没有交接", () => {
    sessionStorage.setItem("deckcraft:handoff", "{ not json");
    expect(takeHandoff()).toBeNull();
  });
});
