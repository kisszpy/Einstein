import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ErrorBoundary } from "./ErrorBoundary";

/**
 * 错误边界是「白屏」的唯一防线，这几条用例守的是：
 * 1. 子组件抛错时给兜底 UI，而不是把整棵树卸载掉；
 * 2. chunk 加载失败（发版后旧 hash）要给出「刷新」而不是「重试」的死胡同；
 * 3. 重试按钮真的能恢复渲染。
 */

function Boom({ fail }: { fail: boolean }) {
  if (fail) throw new Error("组件炸了");
  return <p>正常内容</p>;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ErrorBoundary", () => {
  it("正常时不拦截渲染", () => {
    render(
      <ErrorBoundary>
        <Boom fail={false} />
      </ErrorBoundary>,
    );
    expect(screen.getByText("正常内容")).toBeInTheDocument();
  });

  it("子组件抛错时展示兜底文案，而不是白屏", () => {
    // 故意抛错，React 会打一屏堆栈；这一步只关心兜底 UI 是否出现
    vi.spyOn(console, "error").mockImplementation(() => {});

    render(
      <ErrorBoundary>
        <Boom fail />
      </ErrorBoundary>,
    );

    expect(screen.getByText("这个页面出错了")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "刷新页面" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "复制错误" })).toBeInTheDocument();
    // 错误信息要留在界面上，否则用户截图给不出任何线索
    expect(screen.getByText(/组件炸了/)).toBeInTheDocument();
  });

  it("识别 chunk 加载失败，只给刷新（旧资源地址重试也没用）", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});

    function ChunkBoom(): never {
      throw new Error("Failed to fetch dynamically imported module: /assets/KbPage-abc.js");
    }

    render(
      <ErrorBoundary>
        <ChunkBoom />
      </ErrorBoundary>,
    );

    expect(screen.getByText("页面资源加载失败")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "刷新页面" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
  });

  it("点「重试」后能恢复渲染", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});

    let shouldFail = true;
    function Flaky() {
      if (shouldFail) throw new Error("第一次失败");
      return <p>已恢复</p>;
    }

    render(
      <ErrorBoundary>
        <Flaky />
      </ErrorBoundary>,
    );
    expect(screen.getByText("这个页面出错了")).toBeInTheDocument();

    shouldFail = false;
    await userEvent.click(screen.getByRole("button", { name: "重试" }));

    expect(screen.getByText("已恢复")).toBeInTheDocument();
  });
});
