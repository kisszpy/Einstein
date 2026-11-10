import React from "react";

type Props = { children: React.ReactNode };
type State = { error: Error | null; stack: string };

/**
 * 渲染错误兜底。
 *
 * 之前没有任何 ErrorBoundary：任意页面组件抛一次错，React 会把整棵树卸载成
 * 白屏，用户只有一个空窗口，连"回聊天页"都点不了。这里按「单页崩」而不是
 * 「整站崩」来兜——外层的 AppLayout 侧栏仍然活着，换个页面就能继续用。
 */
export class ErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null, stack: "" };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // 留着现场：用户截图能给出错位置；接错误上报时把这里换成 reportError(...)
    console.error("[ui] 渲染异常", error, info.componentStack);
    this.setState({ stack: info.componentStack ?? "" });
  }

  private reset = () => this.setState({ error: null, stack: "" });

  private reload = () => window.location.reload();

  render() {
    const { error, stack } = this.state;
    if (!error) return this.props.children;

    const isChunkError = /dynamically imported module|Loading chunk|Failed to fetch/i.test(
      `${error.name} ${error.message}`,
    );

    return (
      <div className="flex h-full min-h-[320px] items-center justify-center p-6">
        <div className="w-full max-w-[560px] rounded-panel border border-line bg-surface-1 p-6 shadow-card">
          <h1 className="text-[15px] font-medium text-primary">
            {isChunkError ? "页面资源加载失败" : "这个页面出错了"}
          </h1>
          <p className="mt-2 text-[13px] leading-relaxed text-secondary">
            {isChunkError
              ? "多半是版本刚更新，浏览器还拿着旧的资源地址。刷新一次即可。"
              : "其余页面还能正常使用：点左侧导航换个页面试试，或刷新重来。"}
          </p>

          <pre className="mt-3 max-h-[140px] overflow-auto rounded-card bg-surface-3 p-3 text-[11px] leading-relaxed text-tertiary">
            {error.name}: {error.message}
            {stack ? `\n${stack.trim().split("\n").slice(0, 6).join("\n")}` : ""}
          </pre>

          <div className="mt-4 flex gap-2">
            <button
              type="button"
              onClick={this.reload}
              className="rounded-card bg-brand px-3.5 py-2 text-[13px] font-medium text-brand-fg transition-opacity hover:opacity-90"
            >
              刷新页面
            </button>
            {!isChunkError && (
              <button
                type="button"
                onClick={this.reset}
                className="rounded-card border border-line-strong px-3.5 py-2 text-[13px] text-secondary transition-colors hover:bg-hover"
              >
                重试
              </button>
            )}
            <button
              type="button"
              onClick={() => {
                void navigator.clipboard?.writeText(
                  `${error.stack ?? `${error.name}: ${error.message}`}\n${stack}`,
                );
              }}
              className="rounded-card px-3.5 py-2 text-[13px] text-tertiary transition-colors hover:bg-hover"
            >
              复制错误
            </button>
          </div>
        </div>
      </div>
    );
  }
}
