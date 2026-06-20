import { expect, test, type Page } from "@playwright/test";

import { expectNoPageOverflow, mockDatabaseReady } from "./_helpers";

const authStatus = {
  data: { mode: "local", auth_required: false, authenticated: true, user: null, expires_at: null },
  error_messages: [],
  warning_messages: [],
};

function snapshot(linkingSelected: string) {
  return {
    data: {
      adapters: [
        {
          key: "schema_linking",
          settings_field: "nl2sql_schema_linking",
          label: "スキーマリンク (Schema Linking)",
          selected: linkingSelected,
          options: [
            { name: "enforce_all", origin: "default", recommended_for: ["既定"], summary: "全許可表", selected: linkingSelected === "enforce_all" },
            { name: "curated", origin: "manual", recommended_for: ["精選"], summary: "明示選択", selected: linkingSelected === "curated" },
            { name: "auto_prune", origin: "vector", recommended_for: ["大規模"], summary: "ベクトル多段", selected: linkingSelected === "auto_prune" },
          ],
        },
        {
          key: "knowledge",
          settings_field: "nl2sql_knowledge_profile",
          label: "知識/例示 (Knowledge)",
          selected: "off",
          options: [
            { name: "off", origin: "default", recommended_for: ["既定"], summary: "なし", selected: true },
            { name: "few_shot", origin: "vector", recommended_for: ["反復"], summary: "例示注入", selected: false },
          ],
        },
      ],
      config_source: "runtime",
    },
    error_messages: [],
    warning_messages: [],
  };
}

test.beforeEach(async ({ page }) => {
  await page.route("**/api/auth/me", (route) => route.fulfill({ json: authStatus }));
  await mockDatabaseReady(page);
});

test("パイプライン preset 設定は preset を切り替えてランタイム反映する", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  let patched: unknown = null;

  await page.route("**/api/settings/nl2sql/pipeline", (route) =>
    route.fulfill({ json: snapshot("enforce_all") })
  );
  await page.route("**/api/settings/nl2sql/pipeline/schema_linking", async (route) => {
    patched = route.request().postDataJSON();
    await route.fulfill({ json: snapshot("auto_prune") });
  });

  await page.goto("/settings/nl2sql-pipeline");
  await expect(page.getByRole("heading", { name: "パイプライン preset" })).toBeVisible();
  await expect(page.getByRole("radiogroup", { name: "スキーマリンク (Schema Linking)" })).toBeVisible();

  await page.getByRole("radio", { name: /auto_prune/ }).click();

  await expect.poll(() => patched).toEqual({ selection: "auto_prune" });
  await expect(page.getByText(/auto_prune に変更しました/)).toBeVisible();
  await expect(
    page.getByRole("link", { name: "NL2SQL パイプライン (Pipeline)" })
  ).toHaveAttribute("aria-current", "page");

  await expectNoPageOverflow(page);
});

test("パイプライン preset 設定は取得失敗時にエラーを表示する", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.route("**/api/settings/nl2sql/pipeline", (route) =>
    route.fulfill({
      status: 500,
      json: { data: null, error_messages: ["boom"], warning_messages: [] },
    })
  );

  await page.goto("/settings/nl2sql-pipeline");
  await expect(page.getByText("boom")).toBeVisible();
});
