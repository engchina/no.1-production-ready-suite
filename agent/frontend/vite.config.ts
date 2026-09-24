import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { fileURLToPath, URL } from "node:url";

const MISSING_BACKEND_URL_WARNING =
  "[agent] BACKEND_URL が未設定のため /api を proxy せず 404 を返します（hermetic）。backend に接続する場合は BACKEND_URL=http://127.0.0.1:8020 npm run dev のように明示してください。";

export default defineConfig(() => {
  // /api は BACKEND_URL を明示したときだけ proxy する。既定の接続先（localhost:8020 等）は持たない。
  // 未設定のまま起動した dev サーバ（古い worktree、globalSetup の無い Playwright 設定、手動起動）が
  // 利用者の実 backend へ接続し、実環境の設定（~/.oci/config、backend/.env、backend/model-settings.json 等）へ
  // 書き込むことを防ぐ。
  const backendUrl = process.env.BACKEND_URL?.trim() || undefined;
  // Playwright e2e は全 API を page.route で mock する前提（hermetic）。PLAYWRIGHT_HERMETIC_API=1 なら
  // BACKEND_URL があっても proxy しない。
  const hermeticApi = process.env.PLAYWRIGHT_HERMETIC_API === "1" || !backendUrl;
  const warnMissingBackendUrl = process.env.PLAYWRIGHT_HERMETIC_API !== "1" && !backendUrl;

  return {
    plugins: [
      react(),
      {
        name: "agent-playwright-hermetic-api",
        configureServer(server) {
          if (!hermeticApi) return;
          if (warnMissingBackendUrl) server.config.logger.warn(MISSING_BACKEND_URL_WARNING);
          // 未モックの /api は実 backend へ proxy せず 404 で終端する。
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
  };
});
