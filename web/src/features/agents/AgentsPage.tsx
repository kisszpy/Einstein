import { useCallback, useEffect, useRef, useState } from "react";
import { readSseStream } from "@/features/chat/sse-parser";
import { Markdown } from "@/features/chat/components/Markdown";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface ToolInfo {
  id: string;
  name: string;
  description: string;
}

interface AgentStep {
  kind: "tool_call" | "tool_result";
  name: string;
  args?: string;
  content?: string;
}

interface KbInfo {
  id: string;
  name: string;
  document_count: number;
}

interface AgentRunItem {
  id: string;
  task: string;
  tool_ids: string[];
  steps: AgentStep[];
  answer: string;
  status: string;
  created_at: string;
}

export function AgentsPage() {
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [selected, setSelected] = useState<string[]>(["calculator"]);
  const [kbs, setKbs] = useState<KbInfo[]>([]);
  const [selectedKbs, setSelectedKbs] = useState<string[]>([]);
  const [task, setTask] = useState("");
  const [steps, setSteps] = useState<AgentStep[]>([]);
  const [answer, setAnswer] = useState("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runs, setRuns] = useState<AgentRunItem[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);

  const loadRuns = useCallback(async () => {
    const res = await fetch("/api/agent/runs");
    setRuns(await res.json());
  }, []);

  useEffect(() => {
    void loadRuns();
  }, [loadRuns]);

  useEffect(() => {
    void fetch("/api/agent/tools")
      .then((r) => r.json())
      .then(setTools);
    void fetch("/api/kb")
      .then((r) => r.json())
      .then(setKbs);
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [steps, answer]);

  const toggleTool = (id: string) => {
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((t) => t !== id) : [...prev, id],
    );
  };

  const toggleKb = (id: string) => {
    setSelectedKbs((prev) =>
      prev.includes(id) ? prev.filter((k) => k !== id) : [...prev, id],
    );
  };

  const run = useCallback(
    async (taskText: string) => {
      if (!taskText.trim() || running) return;
      setSteps([]);
      setAnswer("");
      setError(null);
      setRunning(true);
      try {
        const res = await fetch("/api/agent/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            task: taskText.trim(),
            tool_ids: selected.filter((t) => t !== "kb_search"),
            kb_ids: selectedKbs,
          }),
        });
        if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

        await readSseStream(res.body, (event, raw) => {
          switch (event) {
            case "step": {
              const { step } = raw as { step: AgentStep };
              setSteps((prev) => [...prev, step]);
              break;
            }
            case "delta": {
              const { text } = raw as { text: string };
              setAnswer((prev) => prev + text);
              break;
            }
            case "error":
              setError((raw as { message: string }).message);
              break;
          }
        });
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          setError(`请求失败：${(err as Error).message}`);
        }
      } finally {
        setRunning(false);
        void loadRuns();
      }
    },
    [selected, selectedKbs, running, loadRuns],
  );

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-[860px] space-y-6 px-8 py-8">
        <div>
          <h1 className="text-lg font-medium">智能体</h1>
          <p className="mt-1 text-[13px] text-secondary">
            ReAct 循环：模型自主决定调用哪些工具，逐步完成任务。过程实时可见。
          </p>
        </div>

        {/* 工具选择 */}
        <Card className="p-5">
          <h3 className="text-[13px] font-medium">工具（{tools.length}）</h3>
          <div className="mt-3 grid grid-cols-2 gap-2">
            {tools.filter((t) => t.id !== "kb_search").map((t) => (
              <button
                key={t.id}
                onClick={() => toggleTool(t.id)}
                className={cn(
                  "rounded-lg border px-3 py-2 text-left transition-colors duration-150",
                  selected.includes(t.id)
                    ? "border-brand bg-brand-soft"
                    : "border-line hover:border-line-strong",
                )}
              >
                <div className={cn("text-[13px]", selected.includes(t.id) ? "text-brand" : "text-primary")}>
                  {t.name}
                </div>
                <div className="text-xs text-tertiary">{t.description}</div>
              </button>
            ))}
          </div>
        </Card>

        {/* 知识库挂载（选中即自动启用检索工具） */}
        {kbs.length > 0 && (
          <Card className="p-5">
            <h3 className="text-[13px] font-medium">
              知识库<span className="ml-2 font-normal text-tertiary">选中后自动启用检索工具</span>
            </h3>
            <div className="mt-3 flex flex-wrap gap-2">
              {kbs.map((k) => (
                <button
                  key={k.id}
                  onClick={() => toggleKb(k.id)}
                  className={cn(
                    "rounded-lg border px-3 py-2 text-left transition-colors duration-150",
                    selectedKbs.includes(k.id)
                      ? "border-brand bg-brand-soft"
                      : "border-line hover:border-line-strong",
                  )}
                >
                  <div className={cn("text-[13px]", selectedKbs.includes(k.id) ? "text-brand" : "text-primary")}>
                    {k.name}
                  </div>
                  <div className="text-xs text-tertiary">{k.document_count} 个文档</div>
                </button>
              ))}
            </div>
          </Card>
        )}

        {/* 任务输入 */}
        <Card className="flex items-end gap-2 p-3">
          <textarea
            value={task}
            onChange={(e) => setTask(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void run(task);
              }
            }}
            placeholder="给智能体一个任务，例如：请计算 (3+4)*12 等于多少"
            rows={2}
            className="max-h-40 flex-1 resize-none bg-transparent px-2 text-sm outline-none placeholder:text-tertiary"
          />
          <Button disabled={running || !task.trim()} onClick={() => void run(task)}>
            {running ? "运行中…" : "运行"}
          </Button>
        </Card>

        {/* 运行过程 */}
        {(steps.length > 0 || answer || error) && (
          <Card className="space-y-2 p-5">
            <h3 className="text-[13px] font-medium">运行过程</h3>
            {steps.map((s, i) => (
              <div
                key={i}
                className={cn(
                  "rounded-lg px-3 py-2 text-[13px]",
                  s.kind === "tool_call" ? "bg-brand-soft" : "bg-surface-3",
                )}
              >
                {s.kind === "tool_call" ? (
                  <span className="text-primary">
                    <span className="text-brand">调用工具</span> · {s.name}
                    <code className="ml-2 text-xs text-secondary">{s.args}</code>
                  </span>
                ) : (
                  <span className="text-primary">
                    <span className="text-success">工具返回</span> · {s.name}
                    <span className="ml-2 text-xs text-secondary">{s.content}</span>
                  </span>
                )}
              </div>
            ))}

            {answer && (
              <div className="mt-3 border-t border-line pt-3">
                <p className="mb-1 text-xs text-tertiary">最终回答</p>
                <Markdown content={answer} />
              </div>
            )}
            {error && (
              <p className="rounded-lg bg-danger/10 px-3 py-2 text-xs text-danger">{error}</p>
            )}
            <div ref={bottomRef} />
          </Card>
        )}

        {/* 运行历史 */}
        {runs.length > 0 && (
          <Card className="p-5">
            <h3 className="text-[13px] font-medium">运行历史（{runs.length}）</h3>
            <div className="mt-3 space-y-1">
              {runs.map((r) => (
                <button
                  key={r.id}
                  onClick={() => {
                    setSteps(r.steps);
                    setAnswer(r.answer);
                    setError(null);
                    setTask(r.task);
                    setSelected(r.tool_ids);
                  }}
                  className="block w-full rounded-lg px-3 py-2 text-left transition-colors duration-150 hover:bg-surface-3"
                >
                  <div className="truncate text-[13px] text-primary">{r.task}</div>
                  <div className="text-xs text-tertiary">
                    {new Date(r.created_at).toLocaleString("zh-CN")} · {r.steps.length} 步 ·{" "}
                    {r.tool_ids.join(", ") || "无工具"}
                  </div>
                </button>
              ))}
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}
