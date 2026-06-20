import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { fileURLToPath, URL } from "node:url";

const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8010";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
    // 共有 UI パッケージ（file: リンク）の React 重複を防ぐ（"Invalid hook call" 回避）。
    dedupe: ["react", "react-dom"],
  },
  server: {
    host: "0.0.0.0",
    port: 3001,
    proxy: {
      "/api": { target: backendUrl, changeOrigin: true },
    },
  },
  preview: {
    host: "0.0.0.0",
    port: 3001,
  },
});
