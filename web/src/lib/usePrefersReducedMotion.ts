import { useEffect, useState } from "react";

/**
 * 系统是否要求「减少动效」。Canvas 与 CSS 动画都要据此降级，
 * 否则前庭敏感用户会被强行晃到。
 */
export function usePrefersReducedMotion(): boolean {
  const [reduce, setReduce] = useState(false);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduce(mq.matches);
    const onChange = () => setReduce(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  return reduce;
}
