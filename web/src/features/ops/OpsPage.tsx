import { useState } from "react";
import { AlertsTab } from "./AlertsTab";
import { UsageTab } from "./UsageTab";
import { cn } from "@/lib/utils";

const RANGES = [7, 30, 90] as const;

export function OpsPage() {
  const [tab, setTab] = useState<"usage" | "alerts">("usage");
  const [days, setDays] = useState<number>(7);

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-[960px] space-y-5 px-8 py-8">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-lg font-medium">运营</h1>
            <p className="mt-1 text-[13px] text-secondary">
              用量与告警。看的是这台部署实际花了多少、什么时候该叫人。
            </p>
          </div>
          <div className="flex shrink-0 gap-1 rounded-lg border border-line bg-surface-2 p-1">
            {RANGES.map((d) => (
              <button
                key={d}
                onClick={() => setDays(d)}
                className={cn(
                  "rounded-md px-2.5 py-1 text-[12.5px] transition-colors duration-150",
                  days === d
                    ? "bg-surface-1 text-primary shadow-card"
                    : "text-secondary hover:text-primary",
                )}
              >
                {d} 天
              </button>
            ))}
          </div>
        </div>

        <div className="flex w-fit gap-1 rounded-lg border border-line bg-surface-2 p-1">
          {(
            [
              { id: "usage", label: "用量" },
              { id: "alerts", label: "告警" },
            ] as const
          ).map((item) => (
            <button
              key={item.id}
              onClick={() => setTab(item.id)}
              className={cn(
                "rounded-md px-3 py-1.5 text-[12.5px] transition-colors duration-150",
                tab === item.id
                  ? "bg-surface-1 text-primary shadow-card"
                  : "text-secondary hover:text-primary",
              )}
            >
              {item.label}
            </button>
          ))}
        </div>

        {tab === "usage" ? <UsageTab days={days} /> : <AlertsTab />}
      </div>
    </div>
  );
}
