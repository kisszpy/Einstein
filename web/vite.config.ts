import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
// 用 vitest 的 defineConfig：它继承 Vite 的配置类型并补上 `test` 字段。
// 从 "vite" 导入的话 `test` 会被 TS 判成未知属性。
import { defineConfig } from "vitest/config";
import pkg from "./package.json";

// 版本号唯一来源是 package.json：界面里写 __APP_VERSION__，不要手写 "v0.x.y"
export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
  resolve: {
    alias: {
      "@": "/src",
    },
  },
  server: {
    port: 5173,
    // 后端 FastAPI 起在 8000，/api 代理过去
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    // 组件测试要 DOM；纯函数测试跑在同一环境里也没有副作用
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    // 样式一律桩掉：测的是行为与文案，不是 Tailwind 算出来的颜色
    css: false,
    // 单线程跑：多 worker 时 vitest 要往系统临时目录写进程间通信文件，
    // 在受限宿主（工作区外的 fs 被拦截）上会 EPERM，表现为「只跑了一个测试文件」
    // 且不报失败——比慢更危险。用例规模还小，单线程仍是秒级。
    pool: "threads",
    poolOptions: { threads: { singleThread: true } },
  },
});
