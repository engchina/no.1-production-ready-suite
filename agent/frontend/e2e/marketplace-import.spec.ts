import { expect, test, type MockApi } from "./fixtures/mock-api";
import type { Page } from "@playwright/test";
import { dbUser } from "./fixtures/auth";

const MARKET = "external-source";
const PLUGIN = "external-plugin";
const manifest = {
  id: PLUGIN,
  name: "文書の分析",
  version: "1.0.0",
  skills: [
    {
      id: "external-skill",
      name: "文書の比較",
      description: "根拠を確認する",
      instructions: "参照文書を読んで比較する",
    },
  ],
  resources: [{ id: "external-reference", name: "REFERENCE.md", content: "対象範囲を確認する。" }],
  mcp_servers: [],
  import_metadata: { repository: "sample/catalog", revision: "a".repeat(40) },
};
const preview = {
  manifest,
  digest: "reviewed-content",
  warnings: ["scripts は実行しません。対応する MCP ツールが必要です。"],
};

async function setup(page: Page, mockApi: MockApi) {
  mockApi.state.marketplaces.push({
    id: MARKET,
    name: "外部の配布元",
    url: "https://example.test/catalog",
    plugin_count: 2,
    last_error: null,
    refresh_status: "ready",
    revision: "a".repeat(40),
  });
  await page.route(`**/api/plugins/marketplaces/${MARKET}/plugins`, (route) =>
    route.fulfill({
      json: {
        data: {
          name: "外部の配布元",
          plugins: [
            {
              catalog_entry: true,
              id: PLUGIN,
              name: manifest.name,
              description: "外部 Skill の配布",
              unavailable_reason: null,
            },
            {
              catalog_entry: true,
              id: "unsupported-plugin",
              name: "未対応の配布物",
              unavailable_reason: "command source は自動導入できません。",
            },
          ],
        },
        error_messages: [],
        warning_messages: [],
      },
    })
  );
  await page.route(`**/api/plugins/marketplaces/${MARKET}/plugins/${PLUGIN}/preview`, (route) =>
    route.fulfill({ json: { data: preview, error_messages: [], warning_messages: [] } })
  );
  await page.goto(`/plugins/marketplaces?id=${MARKET}`);
  await expect(page.getByText(manifest.name, { exact: true })).toBeVisible();
}

async function reviewPlugin(page: Page) {
  await page.getByTestId(`marketplace-plugin-row-actions-${PLUGIN}`).getByRole("button").click();
  await page.getByRole("menuitem", { name: "導入内容を確認", exact: true }).click();
}

async function installReviewed(page: Page) {
  const actions = page.getByTestId("marketplace-import-actions");
  const direct = actions.getByRole("button", { name: "インストール", exact: true });
  if (await direct.count()) return direct.click();
  await actions.getByRole("button", { name: /その他の操作/ }).click();
  await page.getByRole("menuitem", { name: "インストール", exact: true }).click();
}

for (const theme of ["light", "dark"]) {
  test(`外部導入は内容と制約を確認してから実行し、キャンセルで送信しない (${theme})`, async ({
    page,
    mockApi,
  }, testInfo) => {
    await setup(page, mockApi);
    await page.evaluate((theme) => {
      document.documentElement.dataset.theme = theme;
    }, theme);
    const requests: unknown[] = [];
    await page.route("**/api/plugins", async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      requests.push(route.request().postDataJSON());
      const installed = {
        ...manifest,
        enabled: true,
        marketplace_id: MARKET,
        manifest,
        skill_count: 1,
        resource_count: 1,
        mcp_count: 0,
        warnings: preview.warnings,
        agent_count: 0,
      };
      mockApi.state.plugins.push(installed);
      await route.fulfill({
        json: {
          data: installed,
          error_messages: [],
          warning_messages: [],
        },
      });
    });
    await reviewPlugin(page);
    const region = page.getByTestId("marketplace-import-preview");
    await expect(region).toBeFocused();
    await expect(region).toContainText(preview.warnings[0]);
    await expect(region).toContainText("スキル 1 件 / MCP 0 件 / 参照文書 1 件");
    await expect(region).toContainText("REFERENCE.md");
    const skillSummary = region.locator("summary").filter({ hasText: "文書の比較" });
    await skillSummary.focus();
    await page.keyboard.press("Enter");
    await expect(region.getByText("参照文書を読んで比較する", { exact: true })).toBeVisible();
    await page.keyboard.press("Space");
    await expect(region.getByText("参照文書を読んで比較する", { exact: true })).toBeHidden();
    await page.screenshot({ path: testInfo.outputPath(`marketplace-preview-${theme}.png`) });
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    ).toBeLessThanOrEqual(0);
    expect(await page.locator("main").evaluate((element) => element.scrollWidth - element.clientWidth)).toBe(0);
    await installReviewed(page);
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText("対応する内容のみを導入");
    await dialog.getByRole("button", { name: "キャンセル", exact: true }).click();
    expect(requests).toHaveLength(0);
    await installReviewed(page);
    await dialog.getByRole("button", { name: "インストール", exact: true }).click();
    await expect(page.getByText("プラグインをインストールしました", { exact: true })).toBeVisible();
    await expect(region).toHaveCount(0);
    expect(requests).toEqual([
      { marketplace_id: MARKET, plugin_id: PLUGIN, preview_digest: preview.digest, accept_limitations: true },
    ]);
    await page.goto(`/plugins?id=${PLUGIN}`);
    await expect(page.getByRole("heading", { name: manifest.name, level: 1 })).toBeVisible();
    await expect(page.getByText(preview.warnings[0], { exact: true })).toBeVisible();
    await expect(page.getByText(/REFERENCE.md/).first()).toBeVisible();
    const actions = page.getByTestId("plugin-object-actions");
    const remove = actions.getByRole("button", { name: "アンインストール", exact: true });
    if (await remove.count()) await remove.click();
    else {
      await actions.getByRole("button", { name: /その他の操作/ }).click();
      await page.getByRole("menuitem", { name: "アンインストール", exact: true }).click();
    }
    await page.getByRole("alertdialog").getByRole("button", { name: "アンインストール", exact: true }).click();
    await expect(page.getByText("プラグインをアンインストールしました", { exact: true })).toBeVisible();
    expect(mockApi.state.plugins).toHaveLength(0);
  });
}

test("未対応の配布物と preview の取得失敗を区別する", async ({ page, mockApi }) => {
  await setup(page, mockApi);
  await page.getByTestId("marketplace-plugin-row-actions-unsupported-plugin").getByRole("button").click();
  await expect(page.getByRole("menuitem", { name: "導入内容を確認", exact: true })).toBeDisabled();
  await page.keyboard.press("Escape");
  await page.route(`**/api/plugins/marketplaces/${MARKET}/plugins/${PLUGIN}/preview`, (route) =>
    route.fulfill({
      status: 502,
      json: { data: null, error_messages: ["配布物を取得できませんでした"], warning_messages: [] },
    })
  );
  await reviewPlugin(page);
  await expect(page.getByText("配布物を取得できませんでした", { exact: true })).toBeVisible();
  await expect(page.getByTestId("marketplace-import-preview")).toHaveCount(0);
});

test("閲覧権限だけでは外部配布物を取得・導入する操作を出さない", async ({ page, mockApi }) => {
  mockApi.setCurrentUser(dbUser({ permissions: ["agent.runs.view", "menu.plugin_marketplaces"] }));
  await setup(page, mockApi);
  await expect(page.getByTestId(`marketplace-plugin-row-actions-${PLUGIN}`)).toHaveCount(0);
  await expect(page.getByTestId("marketplace-object-actions")).toHaveCount(0);
  expect(mockApi.requests.some((request) => request.path.endsWith("/preview"))).toBe(false);
});

test("HTTP 200 の更新失敗で成功通知を出さず、前回の一覧を示す", async ({ page, mockApi }) => {
  await setup(page, mockApi);
  await page.route(`**/api/plugins/marketplaces/${MARKET}/refresh`, async (route) => {
    const source = mockApi.state.marketplaces[0];
    source.last_error = "配布元の形式が不正です。";
    source.refresh_status = "failed";
    await route.fulfill({ json: { data: source, error_messages: [], warning_messages: [source.last_error] } });
  });
  await page.getByTestId("marketplace-object-actions").getByRole("button", { name: "更新", exact: true }).click();
  await expect(
    page.getByText("一覧を更新できませんでした。配布元の状態を確認してください。", { exact: true })
  ).toBeVisible();
  await expect(page.getByText("プラグインの一覧を更新しました", { exact: true })).toHaveCount(0);
  await expect(
    page.getByText("前回取得した一覧を表示しています。配布元の最新の内容ではありません。", { exact: true })
  ).toBeVisible();
  await expect(page.getByText(manifest.name, { exact: true })).toBeVisible();
});

test("配布物の確認中は経過表示を出し、重複操作を止める", async ({ page, mockApi }) => {
  await setup(page, mockApi);
  let finish!: () => void;
  const pending = new Promise<void>((resolve) => {
    finish = resolve;
  });
  await page.route(`**/api/plugins/marketplaces/${MARKET}/plugins/${PLUGIN}/preview`, async (route) => {
    await pending;
    await route.fulfill({ json: { data: preview, error_messages: [], warning_messages: [] } });
  });
  await reviewPlugin(page);
  await expect(page.getByTestId("marketplace-preview-processing")).toBeVisible();
  await expect(page.getByTestId("marketplace-import-preview")).toHaveCount(0);
  await page.getByTestId(`marketplace-plugin-row-actions-${PLUGIN}`).getByRole("button").click();
  await expect(page.getByRole("menuitem", { name: "導入内容を確認", exact: true })).toBeDisabled();
  await page.keyboard.press("Escape");
  finish();
  await expect(page.getByTestId("marketplace-import-preview")).toBeVisible();
  await expect(page.getByTestId("marketplace-preview-processing")).toHaveCount(0);
});

test("導入時に版が変わった場合は古い確認内容を閉じて再確認する", async ({ page, mockApi }) => {
  await setup(page, mockApi);
  await page.route("**/api/plugins", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    await route.fulfill({
      status: 409,
      json: {
        data: null,
        error_messages: ["配布物が変わりました。導入内容を再確認してください。"],
        warning_messages: [],
      },
    });
  });
  await reviewPlugin(page);
  await expect(page.getByTestId("marketplace-import-preview")).toBeVisible();
  await installReviewed(page);
  await page.getByRole("alertdialog").getByRole("button", { name: "インストール", exact: true }).click();
  await expect(page.getByText("配布物が変わりました。導入内容を再確認してください。", { exact: true })).toBeVisible();
  await expect(page.getByTestId("marketplace-import-preview")).toHaveCount(0);
  await reviewPlugin(page);
  await expect(page.getByTestId("marketplace-import-preview")).toBeVisible();
});

test("取得成功の空一覧と未取得を区別する", async ({ page, mockApi }) => {
  await setup(page, mockApi);
  mockApi.state.marketplaces[0].refresh_status = "not_fetched";
  mockApi.state.marketplaces[0].plugin_count = 0;
  mockApi.state.marketplaces[0].revision = null;
  await page.route(`**/api/plugins/marketplaces/${MARKET}/plugins`, (route) =>
    route.fulfill({ json: { data: { plugins: [] }, error_messages: [], warning_messages: [] } })
  );
  await page.reload();
  await expect(
    page.getByText("配布元の一覧はまだ取得していません。「更新」で取得してください。", { exact: true })
  ).toBeVisible();
  mockApi.state.marketplaces[0].refresh_status = "ready";
  await page.reload();
  await expect(
    page.getByText("配布元の一覧はまだ取得していません。「更新」で取得してください。", { exact: true })
  ).toHaveCount(0);
  await expect(
    page.getByText("プラグインがありません。「更新」で一覧を読み直してください。", { exact: true })
  ).toBeVisible();
});
