import type { Page } from "@playwright/test";

import { MOCK_NOW, expect, test, type MockApi } from "./fixtures/mock-api";

// #1032: 業務 Agent が（公開中の版だけでも）使うスキルを含むプラグインは、無効化・削除を日本語の理由で断る。

const IN_USE_MESSAGE =
  "このプラグインのスキルは業務 Agent（経理の Agent）が使っています。業務 Agent のスキルから外して公開してから、無効化・削除してください。";

function seedPluginUsedByPublishedAgent(mockApi: MockApi) {
  const manifest = {
    id: "plugin_1032",
    name: "経理のプラグイン",
    version: "1.0.0",
    skills: [{ id: "skill_1032", name: "経理の手順" }],
    mcp_servers: [],
    resources: [],
  };
  mockApi.state.plugins.push({
    id: manifest.id,
    name: manifest.name,
    version: manifest.version,
    description: "",
    author: "",
    enabled: true,
    marketplace_id: null,
    skill_count: 1,
    mcp_count: 0,
    resource_count: 0,
    warnings: [],
    agent_count: 0,
    manifest,
  });
  mockApi.state.skills.push({
    id: "skill_1032",
    name: "経理の手順",
    description: "",
    instructions: "",
    mcp_requirements: [],
    resource_ids: [],
    enabled: true,
    tags: [],
    source: "plugin:plugin_1032",
    created_at: MOCK_NOW,
    updated_at: MOCK_NOW,
  });
  // 下書きからは外したが、公開中の版がまだスキルを使っている業務 Agent。
  mockApi.state.agents.push({
    id: "agent-1032",
    name: "経理の Agent",
    description: "",
    instructions: "",
    skill_ids: [],
    model_id: "",
    migration_required: false,
    tool_names: [],
    enabled: true,
    source: "runtime",
    versioned: true,
    versions: [
      {
        version: 1,
        note: "",
        name: "経理の Agent",
        description: "",
        instructions: "",
        skill_ids: ["skill_1032"],
        model_id: "",
        published_at: MOCK_NOW,
        published_by: "local",
      },
    ],
    published_version: 1,
    unpublished_changes: true,
    created_at: MOCK_NOW,
    updated_at: MOCK_NOW,
  });
}

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth
  );
  expect(overflow).toBeLessThanOrEqual(0);
}

test("業務 Agent が使うプラグインの無効化・アンインストールは日本語の理由を出して断り、プラグインを残す", async ({
  page,
  mockApi,
}) => {
  seedPluginUsedByPublishedAgent(mockApi);
  await page.goto("/plugins?id=plugin_1032");
  const toasts = page.locator("[data-toast-placement]");

  await page.getByTestId("plugin-object-actions").getByRole("button", { name: "無効にする" }).click();
  await expect(toasts).toContainText(IN_USE_MESSAGE);
  await expect(toasts).not.toContainText("referenced by agents");
  expect(mockApi.state.plugins[0].enabled).toBe(true);

  await page.getByTestId("plugin-object-actions-more").click();
  await page.getByRole("menuitem", { name: "アンインストール" }).click();
  await page.getByRole("button", { name: "アンインストール", exact: true }).click();
  await expect.poll(() => mockApi.lastRequest("DELETE", "/api/plugins/plugin_1032")).toBeTruthy();
  await expect(toasts).toContainText(IN_USE_MESSAGE);
  await expect(page).toHaveURL(/\/plugins\?id=plugin_1032$/);
  expect(mockApi.state.plugins.map((plugin) => plugin.id)).toEqual(["plugin_1032"]);
  await expectNoHorizontalOverflow(page);
});
