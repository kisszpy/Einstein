import { useId } from "react";
import { cn } from "@/lib/utils";

/**
 * 品牌标识：爱因斯坦侧脸剪影 × 神经网络头发。
 * 爆炸头化成向外辐射的突触走线，寓意「人的思考延伸进 AI」。
 *
 * 用 currentColor 之外的显式色板，保证 favicon / 深色侧栏 / 登录页三处观感一致；
 * 渐变 id 走 useId，避免同页多实例（侧栏 + 空状态）互相覆盖。
 */
export function BrandMark({
  size = 28,
  className,
  title,
}: {
  size?: number;
  className?: string;
  /** 传了才输出 <title>，纯装饰位置留空更利于读屏 */
  title?: string;
}) {
  const uid = useId().replace(/:/g, "");
  const face = `bm-face-${uid}`;
  const tile = `bm-tile-${uid}`;

  return (
    <svg
      viewBox="0 0 64 64"
      width={size}
      height={size}
      role={title ? "img" : undefined}
      aria-hidden={title ? undefined : true}
      aria-label={title}
      className={cn("shrink-0", className)}
    >
      <defs>
        <linearGradient id={tile} x1="0" y1="0" x2="1" y2="1.1">
          <stop offset="0" stopColor="#4a7cf0" />
          <stop offset="0.55" stopColor="#2f6bdc" />
          <stop offset="1" stopColor="#1f4fb8" />
        </linearGradient>
        <linearGradient id={face} x1="0.2" y1="0" x2="0.8" y2="1">
          <stop offset="0" stopColor="#ffffff" />
          <stop offset="1" stopColor="#dbe7ff" />
        </linearGradient>
      </defs>

      <rect width="64" height="64" rx="15" fill={`url(#${tile})`} />

      {/* 背景电路纹理：低透明度，只在 32px 以上可见 */}
      <g stroke="#ffffff" strokeOpacity="0.16" strokeWidth="1.2" fill="none" strokeLinecap="round">
        <path d="M5 27h7l4 4h7" />
        <path d="M9 46h9l4-4" />
        <path d="M45 59v-6l5-5" />
      </g>
      <g fill="#bfe0ff" fillOpacity="0.55">
        <circle cx="5" cy="27" r="1.4" />
        <circle cx="23" cy="31" r="1.4" />
        <circle cx="9" cy="46" r="1.4" />
        <circle cx="50" cy="48" r="1.4" />
      </g>

      {/* 头发 = 突触走线，端点发光 */}
      <g stroke="#cfe0ff" strokeWidth="2.5" strokeLinecap="round" fill="none">
        <path d="M29 12c-1-4 2-7 6-7" />
        <path d="M34 13c1-3 5-5 9-4" />
        <path d="M38 15.5c3-2 7-1 9 2" />
        <path d="M41 19c3-1 6 0 8 3" />
        <path d="M26 11.5c-2-3-6-4-9-2" />
        <path d="M22.5 13.5c-3-1-6 0-8 3" />
        <path d="M20.3 17c-3 0-5 2-6 5" />
      </g>
      <g fill="#ffffff">
        <circle cx="35" cy="5" r="2.1" />
        <circle cx="43" cy="9" r="2.1" />
        <circle cx="47" cy="17.5" r="2.1" />
        <circle cx="49" cy="22" r="2.1" />
        <circle cx="16" cy="10" r="2.1" />
        <circle cx="13" cy="17" r="2.1" />
        <circle cx="13" cy="22.5" r="2.1" />
      </g>
      {/* 两个青色节点：AI 侧的"活跃突触"，给蓝色一格暖冷对比 */}
      <g fill="#8ce8ff">
        <circle cx="35" cy="5" r="2.1" />
        <circle cx="49" cy="22" r="2.1" />
      </g>

      {/* 面部剪影（朝左） */}
      <path
        fill={`url(#${face})`}
        d="M27 12
          C23.5 12.6 20.8 15.8 20.2 20.6
          C20 22 20.8 22.6 21.2 23.4
          C19.6 25.6 17.6 28.4 16.7 30.2
          C16.3 31 16.9 31.6 17.8 31.5
          L21.2 31
          C21.8 32.8 21 34 20 35.2
          C21.8 36.2 21.8 37.2 20.2 38.4
          C21.8 39.4 21.6 40.6 20.4 41.6
          C22.6 42.6 23.4 44.8 22.6 46.6
          C24.4 49.4 27 50.8 30.4 51.6
          L32 55 L44 55 L44 36
          C46.4 33.6 47 30 46.4 26
          C45.8 21 43.4 17 40 14.6
          C36.6 12.6 31.4 11.4 27 12 Z"
      />
      {/* 胡须与眼窝：用挖空表达，避免在深色底上糊成一块 */}
      <g fill="#2f6bdc" fillOpacity="0.55">
        <path d="M22.4 33.2c2.2 1.4 4.2 1.2 5.6 0.4-1 2.2-3.4 3-5.6 2.4z" />
      </g>
      <circle cx="31.5" cy="26" r="1.15" fill="#2f6bdc" fillOpacity="0.75" />
    </svg>
  );
}
