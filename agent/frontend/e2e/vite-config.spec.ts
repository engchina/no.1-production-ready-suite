import { expect, test } from "@playwright/test";
import type { Plugin, UserConfig, ViteDevServer } from "vite";

import viteConfig from "../vite.config";

// BACKEND_URL 未設定で既定の backend（localhost:8020 等）へ proxy する fallback を戻さないための契約テスト。
// frontend に unit test 基盤がないため、ブラウザを使わない Playwright test として vite.config.ts を直接評価する。
const ENV_KEYS = ["BACKEND_URL", "PLAYWRIGHT_HERMETIC_API"] as const;
type ProxyEnv = Partial<Record<(typeof ENV_KEYS)[number], string>>;

function resolveConfig(env: ProxyEnv): UserConfig {
  const saved: ProxyEnv = Object.fromEntries(ENV_KEYS.map((key) => [key, process.env[key]]));
  const apply = (values: ProxyEnv) => {
    for (const key of ENV_KEYS) {
      if (values[key] === undefined) delete process.env[key];
      else process.env[key] = values[key];
    }
  };
  apply(env);
  try {
    expect(typeof viteConfig, "vite.config.ts は env を解決時に読む関数形式で export する").toBe("function");
    return (viteConfig as (env: { command: "serve"; mode: string }) => UserConfig)({ command: "serve", mode: "development" });
  } finally {
    apply(saved);
  }
}

function startDevServer(config: UserConfig) {
  const plugin = config.plugins?.find(
    (p): p is Plugin => typeof p === "object" && p !== null && "name" in p && p.name === "agent-playwright-hermetic-api",
  );
  const middlewarePaths: string[] = [];
  const warnings: string[] = [];
  const server = {
    middlewares: { use: (path: string) => middlewarePaths.push(path) },
    config: { logger: { warn: (message: string) => warnings.push(message) } },
  };
  if (!plugin) throw new Error("hermetic API plugin が見つかりません");
  (plugin.configureServer as (server: ViteDevServer) => void)(server as unknown as ViteDevServer);
  return { middlewarePaths, warnings };
}

test.describe("vite.config /api proxy", () => {
  test("BACKEND_URL 未設定なら /api を proxy せず、hermetic middleware と警告を出す", () => {
    const config = resolveConfig({});
    expect(config.server?.proxy).toBeUndefined();
    const { middlewarePaths, warnings } = startDevServer(config);
    expect(middlewarePaths).toEqual(["/api"]);
    expect(warnings).toHaveLength(1);
  });

  test("BACKEND_URL を明示したときだけその URL へ proxy する", () => {
    const config = resolveConfig({ BACKEND_URL: "http://127.0.0.1:18999" });
    expect(config.server?.proxy).toEqual({ "/api": { target: "http://127.0.0.1:18999", changeOrigin: true, ws: true } });
    expect(startDevServer(config).middlewarePaths).toEqual([]);
  });

  test("PLAYWRIGHT_HERMETIC_API=1 なら BACKEND_URL があっても proxy せず、警告も出さない", () => {
    const config = resolveConfig({ BACKEND_URL: "http://127.0.0.1:18999", PLAYWRIGHT_HERMETIC_API: "1" });
    expect(config.server?.proxy).toBeUndefined();
    const { middlewarePaths, warnings } = startDevServer(config);
    expect(middlewarePaths).toEqual(["/api"]);
    expect(warnings).toEqual([]);
  });
});
