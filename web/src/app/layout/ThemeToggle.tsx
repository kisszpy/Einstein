import { Moon, Sun } from "lucide-react";
import { useTheme } from "./useTheme";

export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const dark = theme === "dark";
  return (
    <button
      onClick={toggle}
      title={dark ? "切换到亮色" : "切换到暗色"}
      aria-label={dark ? "切换到亮色" : "切换到暗色"}
      className="flex h-8 w-8 items-center justify-center rounded-lg text-secondary transition-colors duration-150 hover:bg-surface-3 hover:text-primary"
    >
      {/* 两枚图标交叉淡入淡出，切换时有"翻转"的暗示而不是干巴巴换字 */}
      <span className="relative flex h-4 w-4 items-center justify-center">
        <Sun
          size={15}
          strokeWidth={1.75}
          className={`absolute transition-all duration-200 ${dark ? "rotate-90 scale-50 opacity-0" : ""}`}
        />
        <Moon
          size={14}
          strokeWidth={1.75}
          className={`absolute transition-all duration-200 ${dark ? "" : "-rotate-90 scale-50 opacity-0"}`}
        />
      </span>
    </button>
  );
}
