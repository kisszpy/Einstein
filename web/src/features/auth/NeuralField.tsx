import { useEffect, useRef } from "react";
import { usePrefersReducedMotion } from "@/lib/usePrefersReducedMotion";

interface Dot {
  x: number;
  y: number;
  vx: number;
  vy: number;
  r: number;
}

const LINK = 152; // 连线半径（px）
const REACH = 146; // 指针斥力半径（px）

/**
 * 背景神经网络：节点缓慢漂移、近邻连线、指针轻推。
 *
 * 纯 canvas，不引第三方依赖；像素密度封顶 2x，节点数按面积取，
 * 页面不可见时停帧，系统要求减少动效时只画一帧静态图。
 * 主题切换时重读 CSS 变量里的颜色（--login-node / --login-link）。
 */
export function NeuralField({ className = "" }: { className?: string }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const reduce = usePrefersReducedMotion();

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let w = 0;
    let h = 0;
    let dots: Dot[] = [];
    let frame = 0;
    let visible = true;
    const pointer = { x: -1e5, y: -1e5 };

    const readColors = () => {
      const cs = getComputedStyle(document.documentElement);
      return {
        node: cs.getPropertyValue("--login-node").trim() || "0,194,154",
        link: cs.getPropertyValue("--login-link").trim() || "0,170,160",
      };
    };
    let colors = readColors();

    const seed = () => {
      const count = Math.min(84, Math.max(24, Math.round((w * h) / 18000)));
      dots = Array.from({ length: count }, () => ({
        x: Math.random() * w,
        y: Math.random() * h,
        vx: (Math.random() - 0.5) * 0.17,
        vy: (Math.random() - 0.5) * 0.17,
        r: 0.7 + Math.random() * 1.4,
      }));
    };

    const step = () => {
      for (const d of dots) {
        d.x += d.vx;
        d.y += d.vy;
        // 出界回绕，避免角上堆成一团
        if (d.x < -24) d.x = w + 24;
        else if (d.x > w + 24) d.x = -24;
        if (d.y < -24) d.y = h + 24;
        else if (d.y > h + 24) d.y = -24;

        const dx = d.x - pointer.x;
        const dy = d.y - pointer.y;
        const d2 = dx * dx + dy * dy;
        if (d2 > 0.5 && d2 < REACH * REACH) {
          const push = (1 - d2 / (REACH * REACH)) * 0.55;
          const len = Math.sqrt(d2);
          d.x += (dx / len) * push;
          d.y += (dy / len) * push;
        }
      }
    };

    const paint = () => {
      if (!w || !h) return;
      ctx.clearRect(0, 0, w, h);

      for (let i = 0; i < dots.length; i++) {
        const a = dots[i] as Dot;
        for (let j = i + 1; j < dots.length; j++) {
          const b = dots[j] as Dot;
          const dx = a.x - b.x;
          const dy = a.y - b.y;
          const d2 = dx * dx + dy * dy;
          if (d2 > LINK * LINK) continue;
          const alpha = (1 - Math.sqrt(d2) / LINK) * 0.24;
          ctx.strokeStyle = `rgba(${colors.link},${alpha.toFixed(3)})`;
          ctx.lineWidth = 0.7;
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.stroke();
        }
      }

      for (const d of dots) {
        ctx.fillStyle = `rgba(${colors.node},0.6)`;
        ctx.beginPath();
        ctx.arc(d.x, d.y, d.r, 0, Math.PI * 2);
        ctx.fill();
      }
    };

    const loop = () => {
      if (!visible) {
        frame = 0;
        return;
      }
      step();
      paint();
      frame = requestAnimationFrame(loop);
    };

    const start = () => {
      if (reduce) {
        paint();
        return;
      }
      if (!frame) frame = requestAnimationFrame(loop);
    };

    const stop = () => {
      if (frame) cancelAnimationFrame(frame);
      frame = 0;
    };

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      w = rect.width;
      h = rect.height;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      seed();
      paint();
    };

    const onPointer = (e: PointerEvent) => {
      const rect = canvas.getBoundingClientRect();
      pointer.x = e.clientX - rect.left;
      pointer.y = e.clientY - rect.top;
    };
    const offPointer = () => {
      pointer.x = -1e5;
      pointer.y = -1e5;
    };
    const onVisibility = () => {
      visible = !document.hidden;
      if (visible) start();
      else stop();
    };
    const themeWatcher = new MutationObserver(() => {
      colors = readColors();
      if (reduce) paint();
    });
    themeWatcher.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });

    const ro = new ResizeObserver(resize);
    ro.observe(canvas);
    window.addEventListener("pointermove", onPointer, { passive: true });
    window.addEventListener("pointerleave", offPointer);
    document.addEventListener("visibilitychange", onVisibility);

    resize();
    start();

    return () => {
      stop();
      ro.disconnect();
      themeWatcher.disconnect();
      window.removeEventListener("pointermove", onPointer);
      window.removeEventListener("pointerleave", offPointer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [reduce]);

  return <canvas ref={canvasRef} aria-hidden className={className} />;
}
