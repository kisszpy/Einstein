/**
 * 独立的内置浏览器页（/browser）。
 *
 * 状态全部放在 URL 查询参数里（tab / project / path / url），刷新不丢、
 * 也方便智能体工作台和对话来源直接带参数跳进来。
 * 真正的三源内容在 BrowserWorkbench 里，智能体页右侧的面板复用同一份。
 */

import { useSearchParams } from "react-router-dom";
import { BrowserWorkbench } from "./BrowserWorkbench";
import type { BrowserSource, BrowserState } from "./types";

const isTab = (value: string | null): value is BrowserSource =>
  value === "artifact" || value === "web" || value === "deck";

export function BrowserPage() {
  const [params, setParams] = useSearchParams();

  const value: BrowserState = {
    tab: isTab(params.get("tab")) ? (params.get("tab") as BrowserSource) : "artifact",
    project: params.get("project") ?? "",
    path: params.get("path") ?? "",
    url: params.get("url") ?? "",
  };

  const patch = (next: Partial<BrowserState>) => {
    setParams(
      (prev) => {
        const merged = new URLSearchParams(prev);
        Object.entries(next).forEach(([key, raw]) => {
          const text = raw ?? "";
          if (text) merged.set(key, text);
          else merged.delete(key);
        });
        return merged;
      },
      { replace: true },
    );
  };

  return (
    <div className="flex h-[calc(100vh-3.5rem)] flex-col">
      <BrowserWorkbench value={value} onChange={patch} />
    </div>
  );
}
