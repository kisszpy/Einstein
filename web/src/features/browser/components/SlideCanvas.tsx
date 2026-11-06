/** 幻灯片画布：按服务端 ppt.py 的版式参数 1:1 换算成 CSS，做到编辑时所见即所得。 */

import { useEffect, useRef } from "react";
import type { DeckSlide, ThemePreset } from "../types";
import { cn } from "@/lib/utils";

const SLIDE_W = 13.333;
const MARGIN = 0.9;

/** 英寸 → 画布宽度百分比。 */
const pct = (inch: number) => `${(inch / SLIDE_W) * 100}%`;
/** 磅 → 容器查询宽度单位（幻灯片宽 960pt，正好是容器的 100cqw）。 */
const pt = (size: number) => `${(size / 960) * 100}cqw`;

const hex = (value: string, fallback: string) => (value ? `#${value}` : fallback);

interface EditableProps {
  value: string;
  onCommit?: (next: string) => void;
  style?: React.CSSProperties;
  className?: string;
  placeholder?: string;
  editable: boolean;
}

/** contentEditable 只在失焦时回写，避免输入过程中被 React 重置光标。 */
function Editable({ value, onCommit, style, className, placeholder, editable }: EditableProps) {
  const ref = useRef<HTMLDivElement | null>(null);
  const focused = useRef(false);

  useEffect(() => {
    const el = ref.current;
    if (el && !focused.current && el.innerText !== value) el.innerText = value;
  }, [value]);

  if (!editable) {
    return (
      <div style={style} className={className}>
        {value}
      </div>
    );
  }
  return (
    <div
      ref={ref}
      contentEditable
      suppressContentEditableWarning
      spellCheck={false}
      data-placeholder={placeholder}
      style={style}
      className={cn(className, "outline-none focus:ring-1 focus:ring-brand/60 rounded-[2px]")}
      onFocus={() => {
        focused.current = true;
      }}
      onBlur={(event) => {
        focused.current = false;
        onCommit?.(event.currentTarget.innerText.replace(/\u00a0/g, " ").trim());
      }}
      onKeyDown={(event) => {
        event.stopPropagation();
        if (event.key === "Enter" && !event.shiftKey) {
          event.preventDefault();
          event.currentTarget.blur();
        }
        if (event.key === "Escape") event.currentTarget.blur();
      }}
    />
  );
}

interface SlideCanvasProps {
  slide: DeckSlide;
  theme: ThemePreset;
  index: number;
  deckTitle: string;
  author: string;
  editable?: boolean;
  onChange?: (patch: Partial<DeckSlide>) => void;
  className?: string;
}

export function SlideCanvas({
  slide,
  theme,
  index,
  deckTitle,
  author,
  editable = false,
  onChange,
  className,
}: SlideCanvasProps) {
  const dark = { bg: hex(theme.bg, "#0F172A"), surface: hex(theme.surface, "#1E293B") };
  const accent = hex(theme.accent, "#6366F1");
  const accent2 = hex(theme.accent2, "#22D3EE");
  const onDark = hex(theme.on_dark, "#F8FAFC");
  const onDarkMuted = hex(theme.on_dark_muted, "#94A3B8");
  const ink = hex(theme.ink, "#0F172A");
  const inkMuted = hex(theme.ink_muted, "#64748B");
  const paper = hex(theme.paper, "#FFFFFF");

  const footerColor = { color: inkMuted };
  const footer = (pageColor: typeof footerColor) => (
    <div
      style={{
        position: "absolute",
        left: pct(MARGIN),
        bottom: pct(0.32),
        width: pct(7),
        fontSize: pt(10),
        ...pageColor,
      }}
    >
      {deckTitle}
    </div>
  );

  const heading = (title: string, kicker: string) => (
    <>
      {kicker ? (
        <>
          <div
            style={{
              position: "absolute",
              left: pct(MARGIN),
              top: pct(0.52),
              width: pct(0.42),
              height: pct(0.065),
              background: accent,
            }}
          />
          <div
            style={{
              position: "absolute",
              left: pct(MARGIN + 0.56),
              top: pct(0.4),
              width: pct(8),
              fontSize: pt(11),
              fontWeight: 700,
              letterSpacing: "0.06em",
              color: accent,
            }}
          >
            {kicker.toUpperCase()}
          </div>
        </>
      ) : (
        <div
          style={{
            position: "absolute",
            left: pct(MARGIN),
            top: pct(0.66),
            width: pct(0.42),
            height: pct(0.075),
            background: accent,
          }}
        />
      )}
      <Editable
        value={title}
        editable={editable}
        placeholder="标题"
        style={{
          position: "absolute",
          left: pct(MARGIN),
          top: pct(kicker ? 0.86 : 0.88),
          width: pct(SLIDE_W - MARGIN * 2),
          fontSize: pt(title.length <= 22 ? 30 : title.length <= 30 ? 26 : 22),
          fontWeight: 700,
          lineHeight: 1.15,
          color: ink,
        }}
        onCommit={(next) => onChange?.({ title: next })}
      />
      <div
        style={{
          position: "absolute",
          left: pct(MARGIN),
          top: pct(1.72),
          width: pct(SLIDE_W - MARGIN * 2),
          height: 1,
          background: inkMuted,
          opacity: 0.5,
        }}
      />
    </>
  );

  const bodyTop = pct(1.95);
  const bodyHeight = pct(7.5 - 2.9);

  let content: React.ReactNode = null;

  switch (slide.layout) {
    case "title": {
      const title = slide.title || deckTitle;
      const variant = slide.variant || "rings";
      const decorations =
        variant === "band" ? (
          <>
            <div
              style={{
                position: "absolute",
                left: 0,
                bottom: 0,
                width: "100%",
                height: pct(1.55),
                background: dark.surface,
              }}
            />
            <div
              style={{
                position: "absolute",
                left: 0,
                bottom: pct(1.55),
                width: "100%",
                height: pct(0.06),
                background: accent,
              }}
            />
            <div
              style={{
                position: "absolute",
                right: pct(2.1),
                top: pct(0.55),
                width: pct(1.15),
                height: pct(1.15),
                borderRadius: "50%",
                background: accent,
              }}
            />
            <div
              style={{
                position: "absolute",
                right: pct(1.55),
                top: pct(1.05),
                width: pct(0.8),
                height: pct(0.8),
                borderRadius: "50%",
                background: accent2,
                opacity: 0.9,
              }}
            />
          </>
        ) : variant === "diag" ? (
          <>
            <div
              style={{
                position: "absolute",
                right: pct(-1.6),
                top: pct(-1.6),
                width: pct(6.2),
                height: pct(6.2),
                borderRadius: "50%",
                background: dark.surface,
              }}
            />
            <div
              style={{
                position: "absolute",
                right: pct(-0.5),
                top: pct(-1.2),
                width: pct(4.0),
                height: pct(4.0),
                borderRadius: "50%",
                background: accent,
              }}
            />
            <div
              style={{
                position: "absolute",
                left: pct(-0.5),
                bottom: pct(0.74),
                width: pct(4.6),
                height: pct(0.16),
                background: accent2,
              }}
            />
          </>
        ) : (
          <>
            <div
              style={{
                position: "absolute",
                left: pct(SLIDE_W - 4.2),
                top: 0,
                width: pct(4.2),
                height: "100%",
                background: dark.surface,
              }}
            />
            <div
              style={{
                position: "absolute",
                left: pct(SLIDE_W - 4.2),
                top: 0,
                width: pct(0.12),
                height: "100%",
                background: accent,
              }}
            />
            <div
              style={{
                position: "absolute",
                left: pct(SLIDE_W - 3.1),
                top: pct(1.6),
                width: pct(2.4),
                height: pct(2.4),
                borderRadius: "50%",
                background: accent,
              }}
            />
            <div
              style={{
                position: "absolute",
                left: pct(SLIDE_W - 2.6),
                top: pct(2.1),
                width: pct(2.4),
                height: pct(2.4),
                borderRadius: "50%",
                background: accent2,
                opacity: 0.9,
              }}
            />
          </>
        );
      content = (
        <>
          <div style={{ position: "absolute", inset: 0, background: dark.bg }} />
          {decorations}
          <Editable
            value={title}
            editable={editable}
            placeholder="主标题"
            style={{
              position: "absolute",
              left: pct(MARGIN),
              top: pct(2.15),
              width: pct(7.4),
              fontSize: pt(title.length <= 16 ? 46 : title.length <= 24 ? 38 : 30),
              fontWeight: 700,
              lineHeight: 1.1,
              color: onDark,
            }}
            onCommit={(next) => onChange?.({ title: next })}
          />
          <Editable
            value={slide.subtitle || ""}
            editable={editable}
            placeholder="副标题"
            style={{
              position: "absolute",
              left: pct(MARGIN),
              top: pct(4.05),
              width: pct(7.4),
              fontSize: pt(18),
              lineHeight: 1.4,
              color: onDarkMuted,
            }}
            onCommit={(next) => onChange?.({ subtitle: next })}
          />
          {variant === "band" ? (
            <div
              style={{
                position: "absolute",
                left: pct(MARGIN),
                bottom: pct(0.45),
                width: pct(8),
                fontSize: pt(13),
                color: onDarkMuted,
              }}
            >
              {[author, new Date().getFullYear() + "." + (new Date().getMonth() + 1)]
                .filter(Boolean)
                .join(" · ")}
            </div>
          ) : (
            <>
              <div
                style={{
                  position: "absolute",
                  left: pct(MARGIN),
                  top: pct(5.6),
                  width: pct(1.1),
                  height: pct(0.06),
                  background: accent,
                }}
              />
              <div
                style={{
                  position: "absolute",
                  left: pct(MARGIN),
                  top: pct(5.85),
                  width: pct(7),
                  fontSize: pt(12),
                  color: onDarkMuted,
                }}
              >
                {author || ""}
              </div>
            </>
          )}
        </>
      );
      break;
    }

    case "section": {
      content = (
        <>
          <div style={{ position: "absolute", inset: 0, background: dark.bg }} />
          <div
            style={{
              position: "absolute",
              left: 0,
              top: 0,
              width: pct(0.16),
              height: "100%",
              background: accent,
            }}
          />
          <Editable
            value={slide.number || String(index + 1).padStart(2, "0")}
            editable={editable}
            placeholder="序号"
            style={{
              position: "absolute",
              left: pct(MARGIN),
              top: pct(1.85),
              width: pct(3),
              fontSize: pt(96),
              fontWeight: 700,
              lineHeight: 1,
              color: dark.surface,
            }}
            onCommit={(next) => onChange?.({ number: next })}
          />
          <Editable
            value={slide.title || ""}
            editable={editable}
            placeholder="章节标题"
            style={{
              position: "absolute",
              left: pct(MARGIN),
              top: pct(3.35),
              width: pct(9.5),
              fontSize: pt(34),
              fontWeight: 700,
              lineHeight: 1.2,
              color: onDark,
            }}
            onCommit={(next) => onChange?.({ title: next })}
          />
          <Editable
            value={slide.subtitle || ""}
            editable={editable}
            placeholder="说明"
            style={{
              position: "absolute",
              left: pct(MARGIN),
              top: pct(4.55),
              width: pct(9.5),
              fontSize: pt(16),
              color: onDarkMuted,
            }}
            onCommit={(next) => onChange?.({ subtitle: next })}
          />
          {footer({ color: onDarkMuted })}
        </>
      );
      break;
    }

    case "two_col": {
      const columns = slide.columns?.length
        ? slide.columns
        : [
            { title: "左边", bullets: [] },
            { title: "右边", bullets: [] },
          ];
      const gap = 0.35;
      const colW = (SLIDE_W - MARGIN * 2 - gap) / 2;
      content = (
        <>
          <div style={{ position: "absolute", inset: 0, background: paper }} />
          {heading(slide.title || "", slide.kicker || "")}
          {columns.slice(0, 2).map((col, i) => {
            const x = MARGIN + i * (colW + gap);
            return (
              <div key={i}>
                <div
                  style={{
                    position: "absolute",
                    left: pct(x),
                    top: pct(2),
                    width: pct(colW),
                    height: pct(7.5 - 2 - 1),
                    background: theme.paper === "FFFFFF" ? "#F4F4F5" : dark.surface,
                  }}
                />
                <div
                  style={{
                    position: "absolute",
                    left: pct(x),
                    top: pct(2),
                    width: pct(colW),
                    height: pct(0.06),
                    background: i === 0 ? accent : accent2,
                  }}
                />
                <Editable
                  value={col.title || ""}
                  editable={editable}
                  placeholder={i === 0 ? "要点" : "标题"}
                  style={{
                    position: "absolute",
                    left: pct(x + 0.35),
                    top: pct(2.4),
                    width: pct(colW - 0.7),
                    fontSize: pt(17),
                    fontWeight: 700,
                    color: ink,
                  }}
                  onCommit={(next) => {
                    const nextCols = columns.map((c, ci) =>
                      ci === i ? { ...c, title: next } : c,
                    );
                    onChange?.({ columns: nextCols });
                  }}
                />
                <div
                  style={{
                    position: "absolute",
                    left: pct(x + 0.35),
                    top: pct(3.05),
                    width: pct(colW - 0.7),
                    height: pct(7.5 - 3.05 - 1),
                    fontSize: pt(col.bullets.length <= 4 ? 15 : 13),
                    color: ink,
                    lineHeight: 1.4,
                  }}
                >
                  {col.bullets.map((item, bi) => (
                    <Editable
                      key={bi}
                      value={item}
                      editable={editable}
                      placeholder="要点"
                      style={{ marginBottom: pct(0.11) }}
                      onCommit={(next) => {
                        const items = next
                          .split("\n")
                          .map((line) => line.trim())
                          .filter(Boolean);
                        const nextCols = columns.map((c, ci) =>
                          ci === i
                            ? {
                                ...c,
                                bullets: c.bullets
                                  .map((b, bj) => (bj === bi ? items[0] ?? "" : b))
                                  .filter(Boolean),
                              }
                            : c,
                        );
                        onChange?.({ columns: nextCols });
                      }}
                    />
                  ))}
                </div>
              </div>
            );
          })}
          {footer(footerColor)}
        </>
      );
      break;
    }

    case "quote": {
      const quote = slide.quote || slide.title || "";
      content = (
        <>
          <div style={{ position: "absolute", inset: 0, background: dark.bg }} />
          <div
            style={{
              position: "absolute",
              left: pct(2.2),
              top: pct(2.55),
              width: pct(0.9),
              height: pct(0.07),
              background: accent,
            }}
          />
          <Editable
            value={quote}
            editable={editable}
            placeholder="一句值得单独占一页的话"
            style={{
              position: "absolute",
              left: pct(2.2),
              top: pct(2.85),
              width: pct(SLIDE_W - 4.4),
              fontSize: pt(quote.length <= 40 ? 30 : quote.length <= 70 ? 24 : 20),
              lineHeight: 1.5,
              fontStyle: "italic",
              color: onDark,
            }}
            onCommit={(next) => onChange?.({ quote: next })}
          />
          <Editable
            value={slide.subtitle || ""}
            editable={editable}
            placeholder="出处"
            style={{
              position: "absolute",
              left: pct(2.2),
              top: pct(5.3),
              width: pct(SLIDE_W - 4.4),
              fontSize: pt(14),
              color: onDarkMuted,
            }}
            onCommit={(next) => onChange?.({ subtitle: next })}
          />
          {footer({ color: onDarkMuted })}
        </>
      );
      break;
    }

    case "table": {
      const headers = slide.headers ?? [];
      const rows = slide.rows ?? [];
      content = (
        <>
          <div style={{ position: "absolute", inset: 0, background: paper }} />
          {heading(slide.title || "", slide.kicker || "")}
          {headers.length === 0 ? (
            <div
              style={{
                position: "absolute",
                left: pct(MARGIN),
                top: pct(2.2),
                fontSize: pt(16),
                color: inkMuted,
              }}
            >
              表格页需要表头与至少一行数据
            </div>
          ) : (
            <table
              style={{
                position: "absolute",
                left: pct(MARGIN),
                top: pct(2),
                width: pct(SLIDE_W - MARGIN * 2),
                borderCollapse: "collapse",
                tableLayout: "fixed",
                fontSize: pt(12),
              }}
            >
              <thead>
                <tr>
                  {headers.map((head, i) => (
                    <th
                      key={i}
                      style={{
                        background: dark.bg,
                        color: onDark,
                        fontWeight: 700,
                        padding: `${pct(0.13)} ${pct(0.14)}`,
                        textAlign: "left",
                        border: `1px solid ${dark.bg}`,
                        fontSize: pt(13),
                      }}
                    >
                      {head}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, ri) => (
                  <tr key={ri}>
                    {headers.map((_, ci) => (
                      <td
                        key={ci}
                        style={{
                          background: ri % 2 ? "#FAFAFA" : "#FFFFFF",
                          color: ink,
                          padding: `${pct(0.1)} ${pct(0.14)}`,
                          border: "1px solid #E5E5E5",
                        }}
                      >
                        {row[ci] ?? ""}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {footer(footerColor)}
        </>
      );
      break;
    }

    case "stat": {
      const stats = slide.stats ?? [];
      const n = Math.max(stats.length, 1);
      const gap = 0.3;
      const cardW = (SLIDE_W - MARGIN * 2 - gap * (n - 1)) / n;
      const hasNote = stats.some((s) => s.note);
      const top = hasNote ? 2.3 : 2.6;
      const cardH = 7.5 - top - 1.35;
      content = (
        <>
          <div style={{ position: "absolute", inset: 0, background: paper }} />
          {heading(slide.title || "", slide.kicker || "")}
          {stats.slice(0, 4).map((item, i) => {
            const x = MARGIN + i * (cardW + gap);
            const color = i % 2 === 0 ? accent : accent2;
            const vSize = item.value.length <= 5 ? 44 : item.value.length <= 9 ? 36 : 28;
            return (
              <div key={i}>
                <div
                  style={{
                    position: "absolute",
                    left: pct(x),
                    top: pct(top),
                    width: pct(cardW),
                    height: pct(cardH),
                    background: theme.paper === "FFFFFF" ? "#F7F7F8" : dark.surface,
                  }}
                />
                <div
                  style={{
                    position: "absolute",
                    left: pct(x),
                    top: pct(top),
                    width: pct(cardW),
                    height: pct(0.07),
                    background: color,
                  }}
                />
                <div
                  style={{
                    position: "absolute",
                    left: pct(x + 0.28),
                    top: pct(top + 0.5),
                    width: pct(cardW - 0.56),
                    fontSize: pt(vSize),
                    fontWeight: 700,
                    lineHeight: 1.05,
                    color,
                  }}
                >
                  {item.value}
                </div>
                {item.label && (
                  <div
                    style={{
                      position: "absolute",
                      left: pct(x + 0.28),
                      top: pct(top + 1.75),
                      width: pct(cardW - 0.56),
                      fontSize: pt(15),
                      fontWeight: 700,
                      color: ink,
                    }}
                  >
                    {item.label}
                  </div>
                )}
                {item.note && (
                  <div
                    style={{
                      position: "absolute",
                      left: pct(x + 0.28),
                      top: pct(top + 2.3),
                      width: pct(cardW - 0.56),
                      height: pct(Math.max(cardH - 2.5, 0.2)),
                      fontSize: pt(12),
                      lineHeight: 1.4,
                      color: inkMuted,
                    }}
                  >
                    {item.note}
                  </div>
                )}
              </div>
            );
          })}
          {footer(footerColor)}
        </>
      );
      break;
    }

    case "chart": {
      const items = slide.items ?? [];
      const parsed = items.map((it) => ({
        ...it,
        num: (() => {
          const m = /-?\d+(?:\.\d+)?/.exec(String(it.value).replace(/,/g, ""));
          return m ? Math.abs(parseFloat(m[0])) : 0;
        })(),
      }));
      const peak = Math.max(...parsed.map((p) => p.num), 0) || 1;
      const labelW = 2.6;
      const valW = 1.15;
      const trackW = SLIDE_W - MARGIN * 2 - labelW - valW;
      const top = 2.15;
      const rowH = Math.min(0.72, (7.5 - top - 1.2) / Math.max(parsed.length, 1));
      content = (
        <>
          <div style={{ position: "absolute", inset: 0, background: paper }} />
          {heading(slide.title || "", slide.kicker || "")}
          {parsed.slice(0, 7).map((bar, i) => {
            const y = top + i * (rowH + 0.18);
            const frac = Math.min(bar.num / peak, 1);
            return (
              <div key={i}>
                <div
                  style={{
                    position: "absolute",
                    left: pct(MARGIN),
                    top: pct(y),
                    width: pct(labelW - 0.2),
                    height: pct(rowH),
                    display: "flex",
                    alignItems: "center",
                    fontSize: pt(14),
                    fontWeight: 700,
                    color: ink,
                  }}
                >
                  {bar.label}
                </div>
                <div
                  style={{
                    position: "absolute",
                    left: pct(MARGIN + labelW),
                    top: pct(y + rowH * 0.22),
                    width: pct(trackW),
                    height: pct(rowH * 0.56),
                    background: theme.paper === "FFFFFF" ? "#ECECEE" : dark.bg,
                  }}
                />
                {frac > 0 && (
                  <div
                    style={{
                      position: "absolute",
                      left: pct(MARGIN + labelW),
                      top: pct(y + rowH * 0.22),
                      width: pct(Math.max(trackW * frac, 0.08)),
                      height: pct(rowH * 0.56),
                      background: i % 2 === 0 ? accent : accent2,
                    }}
                  />
                )}
                <div
                  style={{
                    position: "absolute",
                    left: pct(MARGIN + labelW + trackW + 0.15),
                    top: pct(y),
                    width: pct(valW),
                    height: pct(rowH),
                    display: "flex",
                    alignItems: "center",
                    fontSize: pt(14),
                    fontWeight: 700,
                    color: inkMuted,
                  }}
                >
                  {bar.value}
                </div>
              </div>
            );
          })}
          {slide.note && (
            <div
              style={{
                position: "absolute",
                left: pct(MARGIN),
                bottom: pct(0.75),
                width: pct(SLIDE_W - MARGIN * 2),
                fontSize: pt(11.5),
                fontStyle: "italic",
                color: inkMuted,
              }}
            >
              {slide.note}
            </div>
          )}
          {footer(footerColor)}
        </>
      );
      break;
    }

    case "timeline": {
      const steps = slide.steps ?? [];
      const n = Math.max(steps.length, 1);
      const gap = 0.32;
      const cardW = (SLIDE_W - MARGIN * 2 - gap * (n - 1)) / n;
      const top = 2.75;
      const lineY = top - 0.42;
      content = (
        <>
          <div style={{ position: "absolute", inset: 0, background: paper }} />
          {heading(slide.title || "", slide.kicker || "")}
          <div
            style={{
              position: "absolute",
              left: pct(MARGIN + 0.3),
              top: pct(lineY),
              width: pct(SLIDE_W - MARGIN * 2 - 0.6),
              height: 1.5,
              background: inkMuted,
              opacity: 0.6,
            }}
          />
          {steps.slice(0, 5).map((step, i) => {
            const x = MARGIN + i * (cardW + gap);
            const color = i % 2 === 0 ? accent : accent2;
            return (
              <div key={i}>
                <div
                  style={{
                    position: "absolute",
                    left: pct(x + cardW / 2 - 0.12),
                    top: pct(lineY - 0.12),
                    width: pct(0.24),
                    height: pct(0.24),
                    borderRadius: "50%",
                    background: color,
                  }}
                />
                <div
                  style={{
                    position: "absolute",
                    left: pct(x),
                    top: pct(top),
                    width: pct(cardW),
                    height: pct(7.5 - top - 1.3),
                    background: theme.paper === "FFFFFF" ? "#F7F7F8" : dark.surface,
                  }}
                />
                <div
                  style={{
                    position: "absolute",
                    left: pct(x),
                    top: pct(top),
                    width: pct(cardW),
                    height: pct(0.06),
                    background: color,
                  }}
                />
                {step.label && (
                  <div
                    style={{
                      position: "absolute",
                      left: pct(x + 0.22),
                      top: pct(top + 0.3),
                      width: pct(cardW - 0.44),
                      fontSize: pt(16),
                      fontWeight: 700,
                      color: ink,
                    }}
                  >
                    {step.label}
                  </div>
                )}
                {step.text && (
                  <div
                    style={{
                      position: "absolute",
                      left: pct(x + 0.22),
                      top: pct(top + 0.95),
                      width: pct(cardW - 0.44),
                      fontSize: pt(13),
                      lineHeight: 1.45,
                      color: inkMuted,
                    }}
                  >
                    {step.text}
                  </div>
                )}
              </div>
            );
          })}
          {footer(footerColor)}
        </>
      );
      break;
    }

    case "grid": {
      const cards = slide.cards ?? [];
      const n = Math.max(cards.length, 1);
      const cols = n <= 4 ? 2 : 3;
      const rowsN = Math.ceil(n / cols);
      const gap = 0.3;
      const cardW = (SLIDE_W - MARGIN * 2 - gap * (cols - 1)) / cols;
      const top = 2.05;
      const cardH = Math.min(2.3, (7.5 - top - 1.15 - gap * (rowsN - 1)) / rowsN);
      content = (
        <>
          <div style={{ position: "absolute", inset: 0, background: paper }} />
          {heading(slide.title || "", slide.kicker || "")}
          {cards.slice(0, 6).map((card, i) => {
            const r = Math.floor(i / cols);
            const c = i % cols;
            const x = MARGIN + c * (cardW + gap);
            const y = top + r * (cardH + gap);
            const color = (r + c) % 2 === 0 ? accent : accent2;
            return (
              <div key={i}>
                <div
                  style={{
                    position: "absolute",
                    left: pct(x),
                    top: pct(y),
                    width: pct(cardW),
                    height: pct(cardH),
                    background: theme.paper === "FFFFFF" ? "#F7F7F8" : dark.surface,
                  }}
                />
                <div
                  style={{
                    position: "absolute",
                    left: pct(x),
                    top: pct(y),
                    width: pct(0.07),
                    height: pct(cardH),
                    background: color,
                  }}
                />
                <div
                  style={{
                    position: "absolute",
                    left: pct(x + 0.32),
                    top: pct(y + 0.42),
                    width: pct(0.5),
                    height: pct(0.05),
                    background: color,
                  }}
                />
                {card.title && (
                  <div
                    style={{
                      position: "absolute",
                      left: pct(x + 0.32),
                      top: pct(y + 0.62),
                      width: pct(cardW - 0.64),
                      fontSize: pt(16),
                      fontWeight: 700,
                      color: ink,
                    }}
                  >
                    {card.title}
                  </div>
                )}
                {card.text && (
                  <div
                    style={{
                      position: "absolute",
                      left: pct(x + 0.32),
                      top: pct(y + 1.25),
                      width: pct(cardW - 0.64),
                      height: pct(Math.max(cardH - 1.45, 0.2)),
                      fontSize: pt(12.5),
                      lineHeight: 1.45,
                      color: inkMuted,
                    }}
                  >
                    {card.text}
                  </div>
                )}
              </div>
            );
          })}
          {footer(footerColor)}
        </>
      );
      break;
    }

    case "vs": {
      const gap = 0.7;
      const colW = (SLIDE_W - MARGIN * 2 - gap) / 2;
      const top = 2.0;
      const h = 7.5 - top - 1.0;
      const sides = [
        { data: slide.left, color: accent },
        { data: slide.right, color: accent2 },
      ];
      content = (
        <>
          <div style={{ position: "absolute", inset: 0, background: paper }} />
          {heading(slide.title || "", slide.kicker || "")}
          {sides.map((side, i) => {
            const x = MARGIN + i * (colW + gap);
            const col = side.data ?? { title: "", bullets: [] };
            return (
              <div key={i}>
                <div
                  style={{
                    position: "absolute",
                    left: pct(x),
                    top: pct(top),
                    width: pct(colW),
                    height: pct(h),
                    background: theme.paper === "FFFFFF" ? "#F7F7F8" : dark.surface,
                  }}
                />
                <div
                  style={{
                    position: "absolute",
                    left: pct(x),
                    top: pct(top),
                    width: pct(colW),
                    height: pct(0.06),
                    background: side.color,
                  }}
                />
                {col.title && (
                  <div
                    style={{
                      position: "absolute",
                      left: pct(x + 0.35),
                      top: pct(top + 0.38),
                      width: pct(colW - 0.7),
                      fontSize: pt(17),
                      fontWeight: 700,
                      color: ink,
                    }}
                  >
                    {col.title}
                  </div>
                )}
                <div
                  style={{
                    position: "absolute",
                    left: pct(x + 0.35),
                    top: pct(top + 1.05),
                    width: pct(colW - 0.7),
                    height: pct(h - 1.4),
                    fontSize: pt(col.bullets.length <= 4 ? 15 : 13),
                    color: ink,
                    lineHeight: 1.4,
                  }}
                >
                  {col.bullets.map((item, bi) => (
                    <div key={bi} style={{ marginTop: bi === 0 ? 0 : pct(0.11) }}>
                      · {item}
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
          <div
            style={{
              position: "absolute",
              left: pct(MARGIN + colW + gap / 2 - 0.33),
              top: pct(top + h / 2 - 0.33),
              width: pct(0.66),
              height: pct(0.66),
              borderRadius: "50%",
              background: dark.bg,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: pt(15),
              fontWeight: 700,
              color: onDark,
            }}
          >
            VS
          </div>
          {footer(footerColor)}
        </>
      );
      break;
    }

    case "end": {
      content = (
        <>
          <div style={{ position: "absolute", inset: 0, background: dark.bg }} />
          <div
            style={{
              position: "absolute",
              left: pct(SLIDE_W - 3.4),
              top: pct(7.5 - 3.4),
              width: pct(2.6),
              height: pct(2.6),
              borderRadius: "50%",
              background: accent,
            }}
          />
          <Editable
            value={slide.title || "谢谢"}
            editable={editable}
            placeholder="谢谢"
            style={{
              position: "absolute",
              left: pct(MARGIN),
              top: pct(2.9),
              width: pct(8),
              fontSize: pt(44),
              fontWeight: 700,
              color: onDark,
            }}
            onCommit={(next) => onChange?.({ title: next })}
          />
          <Editable
            value={slide.subtitle || ""}
            editable={editable}
            placeholder="联系方式 / 一句话"
            style={{
              position: "absolute",
              left: pct(MARGIN),
              top: pct(4.05),
              width: pct(8),
              fontSize: pt(16),
              color: onDarkMuted,
            }}
            onCommit={(next) => onChange?.({ subtitle: next })}
          />
          <div
            style={{
              position: "absolute",
              left: pct(MARGIN),
              top: pct(4.35),
              width: pct(1.1),
              height: pct(0.06),
              background: accent,
            }}
          />
        </>
      );
      break;
    }

    default: {
      const bullets = slide.bullets ?? [];
      const longest = bullets.reduce((max, line) => Math.max(max, line.length), 0);
      const size =
        bullets.length <= 4 && longest <= 34
          ? 20
          : bullets.length <= 6 && longest <= 46
            ? 18
            : bullets.length <= 8
              ? 16
              : 14;
      content = (
        <>
          <div style={{ position: "absolute", inset: 0, background: paper }} />
          {heading(slide.title || "", slide.kicker || "")}
          <div
            style={{
              position: "absolute",
              left: pct(MARGIN),
              top: bodyTop,
              width: pct(SLIDE_W - MARGIN * 2),
              height: bodyHeight,
              color: ink,
            }}
          >
            {bullets.map((item, i) => {
              const sub = /^[-•·]\s|^\s{2}/.test(item);
              return (
                <div
                  key={i}
                  style={{
                    fontSize: pt(sub ? Math.max(size - 3, 12) : size),
                    color: sub ? inkMuted : ink,
                    lineHeight: 1.45,
                    marginTop: i === 0 ? 0 : pct(sub ? 0.08 : 0.19),
                  }}
                >
                  <Editable
                    value={(sub ? "— " : "•  ") + item.replace(/^[-•·]\s+/, "")}
                    editable={editable}
                    placeholder="要点"
                    onCommit={(next) => {
                      const text = next.replace(/^[-•·—]\s*/, "").trim();
                      const list = bullets
                        .map((b, bi) => (bi === i ? text : b))
                        .filter(Boolean);
                      onChange?.({ bullets: list });
                    }}
                  />
                </div>
              );
            })}
          </div>
          <div
            style={{
              position: "absolute",
              left: pct(MARGIN),
              bottom: pct(0.32),
              width: pct(7),
              fontSize: pt(10),
              ...footerColor,
            }}
          >
            {deckTitle}
          </div>
          <div
            style={{
              position: "absolute",
              right: pct(MARGIN),
              bottom: pct(0.32),
              fontSize: pt(10),
              ...footerColor,
            }}
          >
            {String(index + 1).padStart(2, "0")}
          </div>
        </>
      );
      break;
    }
  }

  return (
    <div
      className={cn("relative overflow-hidden bg-white shadow-[0_1px_3px_rgb(0_0_0/0.12)]", className)}
      style={{
        aspectRatio: "13.333 / 7.5",
        containerType: "inline-size",
        fontSize: pt(12),
      }}
      onClick={(event) => {
        if (!editable) return;
        const target = event.target as HTMLElement;
        if (target.isContentEditable) return;
        // 点空白处聚焦第一个可编辑块，方便直接开始改
        const first = event.currentTarget.querySelector<HTMLElement>('[contenteditable="true"]');
        first?.focus();
      }}
    >
      {content}
    </div>
  );
}
