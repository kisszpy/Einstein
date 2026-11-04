import { useEffect, useState } from "react";

type Theme = "light" | "dark";

function currentTheme(): Theme {
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(currentTheme);

  const toggle = () => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    document.documentElement.classList.toggle("dark", next === "dark");
    localStorage.setItem("theme", next);
    setTheme(next);
  };

  useEffect(() => {
    // 跟随系统（未手动选择时）
    if (!localStorage.getItem("theme")) {
      const mq = window.matchMedia("(prefers-color-scheme: dark)");
      const onChange = () => {
        document.documentElement.classList.toggle("dark", mq.matches);
        setTheme(currentTheme());
      };
      mq.addEventListener("change", onChange);
      return () => mq.removeEventListener("change", onChange);
    }
  }, []);

  return { theme, toggle };
}
