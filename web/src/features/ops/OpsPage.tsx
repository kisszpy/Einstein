import { useState } from "react";
import { AlertsTab } from "./AlertsTab";
import { UsageTab } from "./UsageTab";
import { PageBody, PageHeader } from "@/components/ui/page-header";
import { cn } from "@/lib/utils";

const RANGES = [7, 30, 90] as const;

export function OpsPage() {
  const [tab, setTab] = useState<"usage" | "alerts">("usage");
  const [days, setDays] = useState<number>(7);

  return (
    <div className="h-full overflow-y-auto">
      <PageBody width="narrow" className="space-y-5">
        <PageHeader
          title="运营"
          description="用量与告警。看的是这台部署实际花了多少、什么时候该叫人。"
          actions={
            <div className="flex shrink-0 gap-1 rounded-lg border border-line bg-surface-2 p-1">
              {RANGES.map((d) => (
                <button
                  key={d}
                  onClick={() => setDays(d)}
                  className={cn(
                    "rounded-md px-2.5 py-1 text-[12.5px] transition-colors duration-[var(--dur-fast)]",
                    days === d
                      ? "bg-surface-1 font-medium text-primary shadow-card"
                      : "text-secondary hover:text-primary",
                  )}
                >
                  {d} 天
                </button>
              ))}
            </div>
          }
        />

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
                "rounded-md px-3 py-1.5 text-[12.5px] transition-colors duration-[var(--dur-fast)]",
                tab === item.id
                  ? "bg-surface-1 font-medium text-primary shadow-card"
                  : "text-secondary hover:text-primary",
              )}
            >
              {item.label}
            </button>
          ))}
        </div>

        {tab === "usage" ? <UsageTab days={days} /> : <AlertsTab />}
      </PageBody>
    </div>
  );
}
