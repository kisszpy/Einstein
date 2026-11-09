import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
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
});
