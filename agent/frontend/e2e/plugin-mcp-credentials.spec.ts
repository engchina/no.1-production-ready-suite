import { expect, test } from "./fixtures/mock-api";

// プラグインの詳細は MCP サーバーの資格情報を表示しない（backend は設定済みかだけを返し、URL を伏せる。#1081）。
const API_KEY = "sk-plugin-e2e-1081";
const URL_PASSWORD = "url-password-e2e-1081";

test("プラグインの詳細の MCP サーバーは資格情報を出さず、設定済みかだけを表示する", async ({ page }) => {
  await page.goto("/plugins?id=new");
  await page.locator("#plugin-manifest").fill(
    JSON.stringify({
      id: "secret_plugin",
      name: "資格情報を持つプラグイン",
      mcp_servers: [
        {
          server_id: "secret_plugin_mcp",
          base_url: `https://svc:${URL_PASSWORD}@mcp.example.test/jsonrpc?api_key=${API_KEY}&tenant=a`,
          auth_mode: "api_key",
          api_key: API_KEY,
        },
      ],
    })
  );
  await page.getByRole("button", { name: "インストール", exact: true }).click();
  await expect(page.getByText("プラグインをインストールしました")).toBeVisible();
  await expect(page).toHaveURL(/\/plugins\?id=secret_plugin$/);

  const mcpPanel = page.locator("pre").filter({ hasText: "secret_plugin_mcp" });
  await expect(mcpPanel).toContainText('"api_key_configured": true');
  await expect(mcpPanel).toContainText("https://***@mcp.example.test/jsonrpc?api_key=***&tenant=a");
  const body = await page.locator("body").innerText();
  expect(body).not.toContain(API_KEY);
  expect(body).not.toContain(URL_PASSWORD);

  // 再読み込みしても（GET /api/plugins/{id}）同じ形で表示する。
  await page.reload();
  await expect(page.locator("pre").filter({ hasText: "secret_plugin_mcp" })).toContainText(
    '"api_key_configured": true'
  );
  expect(await page.locator("body").innerText()).not.toContain(API_KEY);
  // 375px でも長い URL・JSON で横にはみ出さない。
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
