import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { fileURLToPath, URL } from "node:url";

const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8020";
// Playwright e2e は全 API を page.route で mock する前提（hermetic）。
// 未モックの /api は実 backend へ proxy せず 404 で終端し、利用者の実環境の設定
// （~/.oci/config、backend/.env、backend/model-settings.json 等）へ書き込ませない。
const hermeticApi = process.env.PLAYWRIGHT_HERMETIC_API === "1";

export default defineConfig({
  plugins: [
    react(),
    {
      name: "agent-playwright-hermetic-api",
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
    port: 3002,
    // hermetic では proxy 自体を持たない（WebSocket の upgrade も実 backend へ流さない）。
    proxy: hermeticApi
      ? undefined
      : {
          "/api": { target: backendUrl, changeOrigin: true, ws: true },
        },
  },
  preview: {
    host: "0.0.0.0",
    port: 3002,
  },
});
