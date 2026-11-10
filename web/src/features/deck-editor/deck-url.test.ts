import { describe, expect, it } from "vitest";

import { deckFileName } from "./deck-url";

/**
 * 回归：下载地址 `/api/projects/x/download?path=deck.pptx&ticket=…` 不能整串被当成文件名。
 * 之前 `url.split("/").pop()` 得到的是 `download?path=deck.pptx&ticket=eyJ…`，于是编辑器
 * 标题、日志、另存文件名里全是这串东西。
 */
describe("deckFileName", () => {
  it("从下载地址取 path 参数，而不是整条查询串", () => {
    expect(deckFileName("/api/projects/demo/download?path=deck%2Fdemo.pptx&ticket=eyJhbGci")).toBe(
      "demo.pptx",
    );
  });

  it("普通静态路径取最后一段", () => {
    expect(deckFileName("/samples/rare.pptx")).toBe("rare.pptx");
  });

  it("中文文件名解码后返回", () => {
    expect(
      deckFileName(
        "/api/projects/demo/download?path=%E4%BA%A7%E5%93%81%E4%BB%8B%E7%BB%8D.pptx&ticket=t",
      ),
    ).toBe("产品介绍.pptx");
  });

  it("取不到时给可读兜底，不抛错", () => {
    expect(deckFileName("")).toBe("演示文稿.pptx");
    expect(deckFileName("/api/projects/x/download?path=%2F&ticket=t")).toBe("演示文稿.pptx");
  });
});
