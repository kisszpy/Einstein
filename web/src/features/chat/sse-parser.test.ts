import { describe, expect, it } from "vitest";

import { readSseStream } from "./sse-parser";

/**
 * 流式对话的全部输出都经过这里解析，它出错的表现是「回答丢字/串字/半截」，
 * 而且是网络分片下才复现——所以重点压在**跨 chunk 边界**上。
 */

function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(enc.encode(chunk));
      controller.close();
    },
  });
}

/** 把字符串按字节切片后逐块喂入，模拟 TCP 分片（中文会被切成半个字符）。 */
function byteSplitStream(text: string, size: number): ReadableStream<Uint8Array> {
  const bytes = new TextEncoder().encode(text);
  const chunks: Uint8Array[] = [];
  for (let i = 0; i < bytes.length; i += size) chunks.push(bytes.slice(i, i + size));
  return new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(c);
      controller.close();
    },
  });
}

async function collect(stream: ReadableStream<Uint8Array>) {
  const events: Array<{ event: string; data: unknown }> = [];
  await readSseStream(stream, (event, data) => events.push({ event, data }));
  return events;
}

describe("readSseStream", () => {
  it("解析单个事件", async () => {
    const events = await collect(streamOf(['event: delta\ndata: {"text":"你好"}\n\n']));
    expect(events).toEqual([{ event: "delta", data: { text: "你好" } }]);
  });

  it("一个 chunk 里多个事件按顺序全部派发", async () => {
    const events = await collect(
      streamOf([
        'event: delta\ndata: {"i":1}\n\nevent: delta\ndata: {"i":2}\n\nevent: done\ndata: {"ok":true}\n\n',
      ]),
    );
    expect(events.map((e) => e.event)).toEqual(["delta", "delta", "done"]);
    expect(events.map((e) => e.data)).toEqual([{ i: 1 }, { i: 2 }, { ok: true }]);
  });

  it("事件被拆到多个 chunk 也能拼回来", async () => {
    const events = await collect(
      streamOf(['event: del', 'ta\ndata: {"te', 'xt":"拼好了"}\n', "\n"]),
    );
    expect(events).toEqual([{ event: "delta", data: { text: "拼好了" } }]);
  });

  it("中文被按字节切开（半个 UTF-8 字符）不会乱码", async () => {
    const payload = 'event: delta\ndata: {"text":"一次说完这句话"}\n\n';
    // 每 3 字节一片，必然切在多字节字符中间
    const events = await collect(byteSplitStream(payload, 3));
    expect(events).toEqual([{ event: "delta", data: { text: "一次说完这句话" } }]);
  });

  it("多行 data 按 SSE 规范拼接成一条", async () => {
    // 服务端把长 JSON 折行发送时，data 行要首尾相接（不带换行符）
    const events = await collect(
      streamOf(['event: meta\ndata: {"a":1,\ndata: "b":2}\n\n']),
    );
    expect(events).toEqual([{ event: "meta", data: { a: 1, b: 2 } }]);
  });

  it("非法 JSON 只跳过该块，不影响后续事件", async () => {
    const events = await collect(
      streamOf(['event: delta\ndata: {坏掉的\n\n', 'event: delta\ndata: {"ok":1}\n\n']),
    );
    expect(events).toEqual([{ event: "delta", data: { ok: 1 } }]);
  });

  it("没有结尾空行时残留缓冲也要派发", async () => {
    const events = await collect(streamOf(['event: done\ndata: {"end":true}']));
    expect(events).toEqual([{ event: "done", data: { end: true } }]);
  });

  it("没写 event 行时默认事件名为 message", async () => {
    const events = await collect(streamOf(['data: {"x":1}\n\n']));
    expect(events).toEqual([{ event: "message", data: { x: 1 } }]);
  });
});
