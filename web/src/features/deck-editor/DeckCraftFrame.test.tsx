import { useState } from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DeckCraftFrame } from "./DeckCraftFrame";

/**
 * 回归：父组件每次渲染都传**新的**内联回调（这是最自然的写法，也是线上卡死的成因），
 * 引擎必须只被投喂一次，不能因为回调身份变化而重新 fetch / 重新 open。
 *
 * 历史 bug：回调进依赖 → open 身份变化 → deckUrl 的 effect 每次渲染重跑 → open 里又写日志
 * 触发 setState → 自激循环，CPU 打满、编辑区卡死（/deckcraft 页因为用的是 useCallback
 * 稳定函数所以没有暴露）。
 */

const DECK = "/api/projects/demo/download?path=deck%2Fdemo.pptx&ticket=t";

function Host() {
  const [tick, setTick] = useState(0);
  return (
    <div>
      <button onClick={() => setTick((n) => n + 1)}>bump</button>
      <span data-testid="tick">{tick}</span>
      <DeckCraftFrame
        deckUrl={DECK}
        onReady={() => undefined}
        onDirty={() => undefined}
        onSave={() => undefined}
        onLog={() => undefined}
      />
    </div>
  );
}

/** 模拟引擎发来的 deckcraft:ready（jsdom 不会自己派发 iframe → 父窗口的消息） */
function sendReady(container: HTMLElement) {
  const frame = container.querySelector("iframe");
  act(() => {
    window.dispatchEvent(
      new MessageEvent("message", {
        data: { type: "deckcraft:ready", version: "0.16.0", appVersion: "test" },
        origin: window.location.origin,
        source: frame?.contentWindow ?? null,
      }),
    );
  });
}

describe("DeckCraftFrame 投喂纪律", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("父组件反复渲染不会重复拉取同一份 pptx", async () => {
    const fetchMock = vi.fn(async (_url: string) => ({
      ok: true,
      arrayBuffer: async () => new ArrayBuffer(8),
    }));
    vi.stubGlobal("fetch", fetchMock);

    const { container } = render(<Host />);
    sendReady(container);

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    // ready 之前先取了一次（排队），ready 之后补发一次：两次是预期上限
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    // 关键断言：连点 3 次让父组件重渲染，回调身份每次都变，但不能再拉取
    for (let i = 0; i < 3; i += 1) {
      act(() => screen.getByText("bump").click());
    }
    expect(screen.getByTestId("tick").textContent).toBe("3");
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls.every(([url]) => url === DECK)).toBe(true);
  });

  it("同一份 deckUrl 重复渲染不重开，换文件才重开", async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(8) }));
    vi.stubGlobal("fetch", fetchMock);

    const { container, rerender } = render(
      <DeckCraftFrame deckUrl={DECK} onLog={() => undefined} />,
    );
    sendReady(container);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    rerender(<DeckCraftFrame deckUrl={DECK} onLog={() => undefined} />);
    expect(fetchMock).toHaveBeenCalledTimes(2);

    rerender(<DeckCraftFrame deckUrl={`${DECK}&v=2`} onLog={() => undefined} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
  });
});
