import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ArtifactView } from "./ArtifactView";
import * as api from "../api";

/**
 * 代码文件的预览链路：默认进高亮只读视图，而不是直接甩一个可编辑的
 * textarea——这是「看产物」和「改产物」的分离。这里锁住默认态和切换态。
 */

vi.mock("../api", () => ({
  readFile: vi.fn(),
  writeFile: vi.fn(),
  previewUrl: vi.fn(() => "http://preview"),
  downloadUrl: vi.fn(() => "http://download"),
}));

const PY = "def hi():\n    return \"x\"\n";

beforeEach(() => {
  vi.mocked(api.readFile).mockResolvedValue({
    path: "src/app.py",
    content: PY,
    bytes: PY.length,
    truncated: false,
  });
  vi.mocked(api.writeFile).mockResolvedValue({ ok: true } as never);
});

describe("ArtifactView 代码预览", () => {
  it("默认渲染高亮只读视图", async () => {
    const { container } = render(
      <ArtifactView project="p" path="src/app.py" ticket="t" onEditDeck={() => {}} />,
    );
    await vi.waitFor(() => expect(container.querySelector(".code-view")).toBeTruthy());
    expect(container.querySelector(".hljs-keyword")).toBeTruthy();
    // 默认态不该出现编辑器
    expect(container.querySelector("textarea")).toBeNull();
  });

  it("点「编辑」切回 textarea，内容保持一致", async () => {
    const { container } = render(
      <ArtifactView project="p" path="src/app.py" ticket="t" onEditDeck={() => {}} />,
    );
    await vi.waitFor(() => expect(container.querySelector(".code-view")).toBeTruthy());
    await userEvent.click(screen.getByRole("button", { name: "编辑" }));
    const box = container.querySelector("textarea") as HTMLTextAreaElement;
    expect(box).toBeTruthy();
    expect(box.value).toBe(PY);
  });
});
