import { useTheme } from "./useTheme";

export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  return (
    <button
      onClick={toggle}
      className="rounded-lg border border-line px-3 py-1.5 text-[13px] text-secondary transition-colors duration-150 hover:border-line-strong hover:text-primary"
    >
      {theme === "dark" ? "亮色" : "暗色"}
    </button>
  );
}
