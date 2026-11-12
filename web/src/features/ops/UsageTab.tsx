import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { StatCard } from "@/components/ui/stat-card";
import { apiGet } from "./api";
import { compact, kindLabel, money, percent } from "./format";
import type { Bucket, CostPayload } from "./types";

export function UsageTab({ days }: { days: number }) {
  const [data, setData] = useState<CostPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    apiGet<CostPayload>(`/api/obs/cost?days=${days}`)
      .then((payload) => {
        if (alive) setData(payload);
      })
      .catch((e: unknown) => {
        if (alive) setError(e instanceof Error ? e.message : "用量加载失败");
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [days, tick]);

  if (error && !data) {
    return (
      <Card className="space-y-2 p-6">
        <p className="text-[13px] text-danger">用量加载失败：{error}</p>
        <Button variant="outline" onClick={() => setTick((n) => n + 1)}>
          重试
        </Button>
      </Card>
    );
  }

  if (!data) {
    return <p className="py-10 text-center text-[12.5px] text-tertiary">加载中…</p>;
  }

  const { totals } = data;
  const unpriced = data.by_model.some((m) => !m.priced);

  return (
    <div className="space-y-4">
      {/* 指标卡 */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Metric label="运行数" value={String(totals.runs)}>
          {totals.errors > 0 && <span className="text-danger">错误 {totals.errors} 次</span>}
        </Metric>
        <Metric
          label="Token"
          value={`${compact(totals.tokens_in)} in / ${compact(totals.tokens_out)} out`}
        />
        <Metric label="花费" value={money(totals.cost)}>
          {unpriced && <span className="text-warning">部分模型未定价</span>}
        </Metric>
        <Metric label="平均耗时" value={`${Math.round(totals.avg_ms)} ms`} />
      </div>

      {/* 按天趋势 */}
      <Card className="p-5">
        <div className="flex items-baseline justify-between gap-3">
          <h3 className="text-[13px] font-medium">按天趋势</h3>
          <span className="text-xs text-tertiary">
            近 {days} 天 · Token 合计 {compact(totals.tokens_in + totals.tokens_out)}
          </span>
        </div>
        {data.by_day.length === 0 ? (
          <p className="py-10 text-center text-[12.5px] text-tertiary">
            近 {days} 天没有运行记录
          </p>
        ) : (
          <DayChart items={data.by_day} />
        )}
      </Card>

      {/* 按模型 */}
      <Card className="p-5">
        <h3 className="text-[13px] font-medium">按模型</h3>
        {unpriced && (
          <p className="mt-1 text-[12px] text-tertiary">
            标注「未定价」的模型还没有填单价，
            <Link to="/models" className="rounded px-0.5 py-1 text-brand-ink underline-offset-2 hover:underline ">
              去模型页
            </Link>
            填写后才会计算金额。
          </p>
        )}
        <table className="data-table mt-3">
          <thead>
            <tr>
              <th>模型</th>
              <th className="num">运行数</th>
              <th className="num">Token 入/出</th>
              <th className="num">花费</th>
              <th className="num">平均耗时</th>
            </tr>
          </thead>
          <tbody>
            {data.by_model.map((m) => (
              <tr key={m.model}>
                <td className="max-w-[220px] truncate">{m.model}</td>
                <td className="num">{m.runs}</td>
                <td className="num">
                  {compact(m.tokens_in)} / {compact(m.tokens_out)}
                </td>
                <td className="num">
                  {m.priced ? (
                    <span className="text-secondary">{money(m.cost)}</span>
                  ) : (
                    <span className="text-warning">未定价</span>
                  )}
                </td>
                <td className="num">{Math.round(m.avg_ms)} ms</td>
              </tr>
            ))}
            {data.by_model.length === 0 && (
              <tr>
                <td colSpan={5} className="py-8 text-center text-tertiary">
                  近 {days} 天没有模型调用
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>

      {/* 按成员 */}
      <Card className="p-5">
        <h3 className="text-[13px] font-medium">按成员</h3>
        <table className="data-table mt-3">
          <thead>
            <tr>
              <th>成员</th>
              <th className="num">运行数</th>
              <th className="num">Token 入/出</th>
              <th className="num">花费</th>
            </tr>
          </thead>
          <tbody>
            {data.by_member.map((m) => (
              <tr key={m.owner_id}>
                <td className="max-w-[260px] truncate">{m.name}</td>
                <td className="num">{m.runs}</td>
                <td className="num">
                  {compact(m.tokens_in)} / {compact(m.tokens_out)}
                </td>
                <td className="num">{money(m.cost)}</td>
              </tr>
            ))}
            {data.by_member.length === 0 && (
              <tr>
                <td colSpan={4} className="py-8 text-center text-tertiary">
                  近 {days} 天没有成员用量
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>

      {/* 按入口 */}
      <Card className="p-5">
        <h3 className="text-[13px] font-medium">按入口</h3>
        <table className="data-table mt-3">
          <thead>
            <tr>
              <th>入口</th>
              <th className="num">运行数</th>
              <th className="w-[45%]">占比</th>
            </tr>
          </thead>
          <tbody>
            {data.by_kind.map((k) => {
              const share = totals.runs > 0 ? k.runs / totals.runs : 0;
              return (
                <tr key={k.kind}>
                  <td>{kindLabel(k.kind)}</td>
                  <td className="num">{k.runs}</td>
                  <td>
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-3">
                        <div
                          className="h-full rounded-full bg-brand"
                          style={{ width: `${Math.max(share * 100, 2)}%` }}
                        />
                      </div>
                      <span className="w-10 shrink-0 text-right tabular-nums text-tertiary">
                        {percent(share)}
                      </span>
                    </div>
                  </td>
                </tr>
              );
            })}
            {data.by_kind.length === 0 && (
              <tr>
                <td colSpan={3} className="py-8 text-center text-tertiary">
                  近 {days} 天没有运行记录
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>

      {error && <p className="text-[12px] text-danger">刷新失败：{error}</p>}
      {loading && <p className="text-[12px] text-tertiary">更新中…</p>}
    </div>
  );
}

function Metric({
  label,
  value,
  children,
}: {
  label: string;
  value: string;
  children?: ReactNode;
}) {
  return <StatCard label={label} value={value} hint={children} />;
}

/** 纯 CSS 柱状图：柱高按当天 token 占峰值的比例，title 里给当天明细。 */
function DayChart({ items }: { items: Array<Bucket & { day: string }> }) {
  const peaks = items.map((d) => d.tokens_in + d.tokens_out);
  const max = Math.max(...peaks, 1);
  const first = items[0]?.day ?? "";
  const last = items[items.length - 1]?.day ?? "";
  const middle = items[Math.floor(items.length / 2)]?.day ?? "";
  const dense = items.length > 14;

  return (
    <>
      <div className="relative mt-5 h-40">
        {/* 网格：50% 一条虚线 + 底部基线，补齐「无坐标系」的柱状图 */}
        <div aria-hidden className="pointer-events-none absolute inset-0">
          <span className="absolute inset-x-0 top-0 border-t border-dashed border-line" />
          <span className="absolute inset-x-0 top-1/2 border-t border-dashed border-line" />
          <span className="absolute inset-x-0 bottom-0 border-t border-line-strong" />
        </div>
        <span className="absolute -top-2 right-0 -translate-y-full text-[11px] tabular-nums text-tertiary">
          峰值 {compact(max)}
        </span>
        <div className="relative flex h-full items-end gap-1">
          {items.map((d) => {
            const tokens = d.tokens_in + d.tokens_out;
            const ratio = tokens / max;
            return (
              <div
                key={d.day}
                title={`${d.day} · ${d.runs} 次运行 · ${tokens} token · ${money(d.cost)}`}
                className="group flex h-full min-w-0 flex-1 items-end justify-center"
              >
                <div
                  className={
                    tokens > 0
                      ? // 限宽 + 向下渐隐：数据只有一天时柱子不再占满整行变成大色块
                        "w-full max-w-7 rounded-t-[3px] bg-linear-to-b from-brand to-brand/30 transition-colors duration-150 group-hover:from-brand-strong"
                      : "w-full max-w-7 bg-line"
                  }
                  style={{ height: tokens > 0 ? `${Math.max(ratio * 100, 2)}%` : "2px" }}
                />
              </div>
            );
          })}
        </div>
      </div>
      {dense ? (
        <div className="mt-1.5 flex justify-between text-[12px] text-tertiary">
          <span>{first.slice(5)}</span>
          <span>{middle.slice(5)}</span>
          <span>{last.slice(5)}</span>
        </div>
      ) : (
        <div className="mt-1.5 flex gap-1">
          {items.map((d) => (
            <div key={d.day} className="min-w-0 flex-1 text-center text-[12px] text-tertiary">
              {d.day.slice(5)}
            </div>
          ))}
        </div>
      )}
    </>
  );
}
