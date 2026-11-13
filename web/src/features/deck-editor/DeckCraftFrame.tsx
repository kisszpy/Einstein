import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef } from "react";

import { deckFileName } from "./deck-url";

/**
 * DeckCraft（Rust/wasm 版）的 iframe 封装。
 *
 * 协议是 DeckCraft 官方文档化的宿主桥（HOSTING.md / craft-web-host）：
 *   应用 → 宿主：deckcraft:ready / deckcraft:save {name, saveAs, bytes} / deckcraft:dirty / deckcraft:failed
 *   宿主 → 应用：deckcraft:open {name, bytes}（transfer 转移所有权）
 * 保存回传的是 **pptx 字节**而不是结构化 JSON：pptx 是交换格式，
 * 需要结构化数据时用 pptxtojson 从字节反解，编辑器不需要重新发明一套模型。
 *
 * 必须同源托管（公开的桥契约要求），所以编辑器 bundle 放在 web/public/deckcraft/。
 */

const EDITOR_PATH = "/deckcraft/index.html?host=parent&blank&webgl";

export interface DeckCraftSaveEvent {
  name: string;
  bytes: ArrayBuffer;
  saveAs: boolean;
}

export interface DeckCraftHandle {
  /** 打开（或替换）一份演示文稿。ready 之前调用会排队，ready 后自动补发。 */
  open: (url: string) => Promise<void>;
}

interface DeckCraftFrameProps {
  deckUrl?: string;
  onReady?: (appVersion: string) => void;
  onDirty?: (dirty: boolean) => void;
  onSave?: (event: DeckCraftSaveEvent) => void;
  onLog?: (line: string) => void;
}

export const DeckCraftFrame = forwardRef<DeckCraftHandle, DeckCraftFrameProps>(
  function DeckCraftFrame(props, ref) {
    const { deckUrl } = props;
    const frameRef = useRef<HTMLIFrameElement | null>(null);
    const readyRef = useRef(false);
    /** 编辑器还没就绪时想打开的地址，ready 后补发 */
    const pendingRef = useRef<string | null>(null);
    /** 这份 iframe 里最近一次打开过的地址：ready 重放、去重都靠它 */
    const openedRef = useRef<string | null>(null);
    /** 请求序号：切换文件时丢弃上一次还在飞的 fetch 结果 */
    const seqRef = useRef(0);

    /**
     * 回调统一放进 ref。
     *
     * 调用方常用内联箭头函数（`onLog={(l) => setLog(...)}`），这意味着每次父组件渲染都会
     * 生成新的回调身份。若把回调写进 `useCallback` / `useEffect` 依赖，`open` 的身份会跟着
     * 变化，`deckUrl` 的 effect 于是每次渲染都重跑 → 重新 fetch 并重开这份 pptx；而 `open`
     * 内部自身要写一条日志（`log` → setState）→ 又触发渲染 → **自激循环**：CPU 打满、编辑区
     * 卡死、每次闪一下。放进 ref 后 `log`/`open` 身份恒定，循环从根上消失。
     */
    const handlers = useRef(props);
    handlers.current = props;

    const log = useCallback((line: string) => {
      handlers.current.onLog?.(line);
    }, []);

    const open = useCallback(
      async (url: string) => {
        const seq = (seqRef.current += 1);
        openedRef.current = url;
        // 日志与文档名都只用文件名：下载地址里带着 path/ticket 参数，整串显示出来很难看，
        // 拿它当 name 还会污染编辑器标题和另存文件名。
        const name = deckFileName(url);
        log(`读取 ${name}`);
        const response = await fetch(url);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const bytes = await response.arrayBuffer();
        // 期间又换了文件：这次结果作废，别再灌进编辑器
        if (seq !== seqRef.current) return;
        if (!readyRef.current) {
          pendingRef.current = url;
          log(`编辑器未就绪，open 已排队（${(bytes.byteLength / 1024).toFixed(0)}KB）`);
          return;
        }
        const win = frameRef.current?.contentWindow;
        if (!win) throw new Error("iframe 未就绪");
        // transfer 会把 bytes 的所有权交出去，之后 byteLength 归零；
        // 大小必须在转移之前取，否则日志永远显示「0KB」，看着像没喂进去。
        const kb = bytes.byteLength / 1024;
        win.postMessage({ type: "deckcraft:open", name, bytes }, location.origin, [bytes]);
        log(`已投喂 ${name}（${kb.toFixed(0)}KB）`);
      },
      [log],
    );

    useImperativeHandle(ref, () => ({ open }), [open]);

    useEffect(() => {
      const frame = frameRef.current;
      if (!frame) return;

      const onMessage = (event: MessageEvent) => {
        if (event.origin !== location.origin || event.source !== frame.contentWindow) return;
        const data = (event.data ?? {}) as Record<string, unknown>;
        switch (data.type) {
          case "deckcraft:ready": {
            readyRef.current = true;
            log(`ready v${data.version}/app ${data.appVersion}`);
            handlers.current.onReady?.(String(data.appVersion ?? ""));
            // iframe 刷新（HMR/重载）后编辑器是空的，把最近一份重投一次，保证内容不丢
            const next = pendingRef.current ?? openedRef.current;
            pendingRef.current = null;
            if (next) {
              void open(next).catch((err: Error) => log(`open 失败：${err.message}`));
            }
            break;
          }
          case "deckcraft:dirty": {
            handlers.current.onDirty?.(Boolean(data.dirty));
            break;
          }
          case "deckcraft:save": {
            const bytes = data.bytes as ArrayBuffer;
            log(`deckcraft:save ${data.name}（${(bytes.byteLength / 1024).toFixed(0)}KB）`);
            handlers.current.onSave?.({
              name: String(data.name ?? "deck.pptx"),
              bytes,
              saveAs: Boolean(data.saveAs),
            });
            break;
          }
          case "deckcraft:failed": {
            log(`failed：${data.error}`);
            break;
          }
          default:
            break;
        }
      };

      window.addEventListener("message", onMessage);
      return () => window.removeEventListener("message", onMessage);
      // log/open 身份恒定：这里只订阅一次，不再随父组件渲染反复解绑重绑
    }, [log, open]);

    // 只有「换了一份文件」才投喂；同一份重复渲染不重开
    useEffect(() => {
      if (!deckUrl || openedRef.current === deckUrl) return;
      void open(deckUrl).catch((err: Error) => log(`open 失败：${err.message}`));
    }, [deckUrl, open, log]);

    return (
      <iframe
        ref={frameRef}
        title="DeckCraft 编辑器"
        src={EDITOR_PATH}
        allow="fullscreen; clipboard-read; clipboard-write"
        allowFullScreen
        className="h-full w-full border-0 bg-[#262626]"
      />
    );
  },
);
