import { expect, type Page, test } from "@playwright/test";
import { mockDatabaseReady } from "./_helpers";

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

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await page.route("**/api/auth/me", async (route) => {
    await route.fulfill({ json: authStatus });
  });
  await mockManyKnowledgeBases(page);
});

test("知識ベースが多い場合は絞り込み・件数・スクロール領域を表示する", async ({ page }) => {
  await page.goto("/search");

  // 件数が多いときだけツールバー(絞り込み入力)が現れる。
  const filter = page.getByLabel("知識ベースを名前で絞り込む");
  await expect(filter).toBeVisible();
  await expect(page.getByText(`${KB_COUNT} / ${KB_COUNT} 件`)).toBeVisible();

  // グリッドは高さ固定でスクロール可能(ページ全体を押し下げない)。
  const scrollable = await page.evaluate(() => {
    const group = document.querySelector('[role="group"][aria-label="知識ベース"]');
    if (!group) return false;
    return group.scrollHeight > group.clientHeight + 1;
  });
  expect(scrollable).toBe(true);

  // 名前で絞り込むと一致しない項目は描画されない。
  await filter.fill("-07");
  await expect(page.getByText("1 / 20 件")).toBeVisible();
  await expect(page.getByText("ナレッジベース-07", { exact: true })).toBeVisible();
  await expect(page.getByText("ナレッジベース-02", { exact: true })).toHaveCount(0);

  await expectNoHorizontalOverflow(page);
});

test("全選択は表示中のすべての知識ベースをリクエストへ含める", async ({ page }) => {
  let searchPayload: Record<string, unknown> | null = null;
  await page.route("**/api/search/stream", async (route) => {
    searchPayload = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      status: 200,
      headers: { "content-type": "text/event-stream" },
      body: searchStreamBody(),
    });
  });

  await page.goto("/search");

  await page.getByRole("button", { name: "全選択" }).click();
  await page.getByLabel("RAG 検索").fill("経費申請の承認フロー");
  await page.getByRole("button", { name: "検索" }).click();

  await expect
    .poll(() => (searchPayload?.knowledge_base_ids as string[] | undefined)?.length)
    .toBe(KB_COUNT);

  // クリアで選択をすべて解除できる(KB ツールバーのクリアは先頭に現れる)。
  await page.getByRole("button", { name: "クリア" }).first().click();
  await expect(page.getByText("すべての知識ベースを対象にします。")).toBeVisible();
});

async function mockManyKnowledgeBases(page: Page) {
  const items = Array.from({ length: KB_COUNT }, (_, index) => {
    const label = String(index + 1).padStart(2, "0");
    return {
      id: `kb-${label}`,
      name: `ナレッジベース-${label}`,
      description: null,
      status: "ACTIVE",
      default_search_mode: "hybrid",
      document_count: index,
      indexed_document_count: index,
      error_document_count: 0,
      searchable_chunk_count: index * 2,
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

function searchStreamBody(): string {
  return [
    `event: metadata\ndata: ${JSON.stringify({
      trace_id: "trace-kb-grid",
      elapsed_ms: 10,
      guardrail_warnings: [],
      diagnostics: {},
    })}\n\n`,
    `event: delta\ndata: ${JSON.stringify({ text: "承認フローを確認しました。" })}\n\n`,
    `event: citations\ndata: ${JSON.stringify([])}\n\n`,
    `event: done\ndata: ${JSON.stringify({ trace_id: "trace-kb-grid" })}\n\n`,
  ].join("");
}

async function expectNoHorizontalOverflow(page: Page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth
    )
  ).toBe(true);
}
