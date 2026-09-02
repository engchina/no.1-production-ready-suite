import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { fileURLToPath, URL } from "node:url";

const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8010";
const hermeticApi = process.env.PLAYWRIGHT_HERMETIC_API === "1";

export default defineConfig({
  plugins: [
    react(),
    {
      name: "nl2sql-playwright-hermetic-api",
      configureServer(server) {
        if (!hermeticApi) return;
        server.middlewares.use("/api", (req, res) => {
          const path = `/api${req.url ?? ""}`;
          res.statusCode = 404;
          res.setHeader("Content-Type", "application/json; charset=utf-8");
          res.end(
            JSON.stringify({
              data: null,
              error_messages: [`e2e unmocked API: ${req.method ?? "GET"} ${path}`],
              warning_messages: [],
            })
          );
        });
      },
    },
  ],
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
