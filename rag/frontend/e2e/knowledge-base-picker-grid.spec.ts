import { expect, type Page, test } from "@playwright/test";

import { expectNoPageOverflow, mockDatabaseReady } from "./_helpers";

const authStatus = {
  data: {
    mode: "local",
    auth_required: false,
    authenticated: true,
    user: null,
    expires_at: null,
  },
  error_messages: [],
  warning_messages: [],
};

const KB_COUNT = 20;
// 空の DEFAULT は表示し、通常 KB の空項目(index 1)だけを隠す。
const EMPTY_COUNT = 1;
const VISIBLE_DEFAULT = KB_COUNT - EMPTY_COUNT;

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await page.route("**/api/auth/me", async (route) => {
    await route.fulfill({ json: authStatus });
  });
  await mockManyKnowledgeBases(page);
});

test("コンボボックスを開くと検索・空KB抑制・スクロール領域を備える", async ({ page }) => {
  await page.goto("/evaluation");

  // 既定はトリガーのみで、リストは畳まれている(ページを押し下げない)。
  const combobox = page.getByRole("combobox", { name: "知識ベース" });
  await expect(combobox).toBeVisible();
  await expect(page.getByRole("listbox", { name: "知識ベース" })).toHaveCount(0);

  await combobox.click();
  const listbox = page.getByRole("listbox", { name: "知識ベース" });
  await expect(listbox).toBeVisible();
  await expect(listbox.getByRole("option").first()).toContainText("DEFAULT");
  await expect(listbox.getByRole("option", { name: /ナレッジベース-20/ })).toContainText("最多");

  // 空(0 文書)の KB は既定で非表示。フッターに非表示件数が出る。
  await expect(page.getByText(`空の知識ベース ${EMPTY_COUNT} 件を非表示中`)).toBeVisible();
  await expect(page.getByText(`${VISIBLE_DEFAULT} / ${KB_COUNT} 件`)).toBeVisible();
  await expect(listbox.getByRole("option")).toHaveCount(VISIBLE_DEFAULT);

  // リストは高さ固定でスクロール可能。
  const scrollable = await page.evaluate(() => {
    const list = document.querySelector('[role="listbox"][aria-label="知識ベース"]');
    if (!list) return false;
    return list.scrollHeight > list.clientHeight + 1;
  });
  expect(scrollable).toBe(true);

  // 「空のKBを隠す」を解除すると空 KB も含め全件表示。
  await page.getByRole("checkbox", { name: "空のKBを隠す" }).uncheck();
  await expect(listbox.getByRole("option")).toHaveCount(KB_COUNT);

  // 名前で絞り込むと一致しない項目は描画されない。
  await combobox.fill("-07");
  await expect(listbox.getByRole("option")).toHaveCount(1);
  await expect(listbox.getByRole("option", { name: /ナレッジベース-07/ })).toBeVisible();

  await expectNoPageOverflow(page);
});

test("表示中をすべて選択しチップで可視化、クリアで解除できる", async ({ page }) => {
  await page.goto("/evaluation");

  const combobox = page.getByRole("combobox", { name: "知識ベース" });
  await combobox.click();

  // 空 KB を含めて全件を選択する。
  await page.getByRole("checkbox", { name: "空のKBを隠す" }).uncheck();
  await page.getByRole("button", { name: "表示中をすべて選択" }).click();

  // 選択は削除可能なチップで可視化される。
  await expect(page.getByLabel("ナレッジベース-07 を選択から外す")).toBeVisible();
  await expect(page.getByText(`${KB_COUNT} 件選択中`).first()).toBeVisible();

  // チップの ✕ で個別に外せる。
  await page.getByLabel("ナレッジベース-07 を選択から外す").click();
  await expect(page.getByText(`${KB_COUNT - 1} 件選択中`).first()).toBeVisible();

  // フッターのクリアで選択をすべて解除できる。
  await page.getByRole("button", { name: "クリア" }).click();
  await expect(page.getByText("すべての知識ベースを対象にします。")).toBeVisible();
});

async function mockManyKnowledgeBases(page: Page) {
  const items = Array.from({ length: KB_COUNT }, (_, index) => {
    const label = String(index + 1).padStart(2, "0");
    const documentCount = index === 1 ? 0 : index;
    return {
      id: `kb-${label}`,
      name: index === 0 ? "DEFAULT" : `ナレッジベース-${label}`,
      description: null,
      status: "ACTIVE",
      default_search_mode: "hybrid",
      document_count: documentCount,
      indexed_document_count: documentCount,
      error_document_count: 0,
      searchable_chunk_count: documentCount * 2,
      created_at: "2026-06-15T00:00:00Z",
      updated_at: "2026-06-15T00:00:00Z",
      archived_at: null,
    };
  });

  await page.route("**/api/knowledge-bases**", async (route) => {
    await route.fulfill({
      json: {
        data: { items, total: items.length, limit: 50, offset: 0, has_next: false },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
}
