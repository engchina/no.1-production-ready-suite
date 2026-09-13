import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { fileURLToPath, URL } from "node:url";

const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8000";
// Playwright e2e は全 API を page.route で mock する前提（hermetic）。
// 未モックの /api は実 backend へ proxy せず 404 で終端し、利用者の実環境の設定
// （~/.oci/config、backend/.env、model-settings.json 等）や DB に触れさせない。
const hermeticApi = process.env.PLAYWRIGHT_HERMETIC_API === "1";

export default defineConfig({
  plugins: [
    react(),
    {
      name: "rag-playwright-hermetic-api",
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
    // 共有 UI パッケージ（file: リンク）は自分の node_modules の React を解決しうるため、
    // React/ReactDOM を必ずこのアプリの 1 コピーへ集約する（"Invalid hook call" 回避）。
    dedupe: ["react", "react-dom"],
  },
  server: {
    host: "0.0.0.0",
    port: 3000,
    // hermetic では proxy 自体を持たない。
    proxy: hermeticApi
      ? undefined
      : {
          "/api": {
            target: backendUrl,
            changeOrigin: true,
          },
        },
  },
  preview: {
    host: "0.0.0.0",
    port: 3000,
    proxy: {
      "/api": {
        target: backendUrl,
        changeOrigin: true,
      },
    },
  },
});
