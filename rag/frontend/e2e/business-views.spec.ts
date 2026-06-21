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

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await page.route("**/api/auth/me", async (route) => {
    await route.fulfill({ json: authStatus });
  });
  await mockKnowledgeBases(page);
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 760 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`業務アシスタント管理は作成フォームを表示し横崩れしない (${viewport.name})`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockBusinessViews(page, []);

    await page.goto("/business-views");

    await expect(
      page.getByRole("heading", { name: "業務アシスタント (Assistant)" })
    ).toBeVisible();
    await expect(page.getByRole("heading", { name: "業務アシスタントを作成" })).toBeVisible();
    await expect(page.getByLabel("名前", { exact: true })).toBeVisible();
    await expect(page.getByText("参照する知識ベース", { exact: false }).first()).toBeVisible();
    // 知識ベースはコンボボックスを開くと候補として現れる。
    await page.getByRole("combobox", { name: "参照する知識ベース" }).click();
    await expect(page.getByRole("option", { name: /社内規程/ })).toBeVisible();
    await expect(page.getByLabel(/persona/)).toBeVisible();
    await expectNoPageOverflow(page);
  });
}

test("業務アシスタントを作成すると参照 KB と方針を含めて POST する", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  let createBody: Record<string, unknown> | null = null;
  await mockBusinessViews(page, [], (body) => {
    createBody = body;
  });

  await page.goto("/business-views");

  await page.getByRole("combobox", { name: "参照する知識ベース" }).click();
  await page.getByRole("option", { name: /社内規程/ }).click();
  await page.getByLabel("名前", { exact: true }).fill("経理アシスタント");
  await page
    .getByLabel(/persona/)
    .fill("あなたは経理規程に詳しいアシスタントです。");
  // 配信モードを fused に切り替える(複数 chunk_set 融合)。
  await page.getByRole("combobox", { name: /配信モード/ }).click();
  await page.getByRole("option", { name: /fused/ }).click();
  await page.getByRole("button", { name: "作成する" }).click();

  await expect
    .poll(() => (createBody?.config as { knowledge_base_ids?: string[] })?.knowledge_base_ids)
    .toEqual(["kb-1"]);
  expect(createBody?.name).toBe("経理アシスタント");
  expect((createBody?.config as { system_prompt?: string })?.system_prompt).toContain(
    "経理規程"
  );
  // 配信モードが POST payload に含まれる。
  expect((createBody?.config as { serving_mode?: string })?.serving_mode).toBe("fused");
});

test("RAG 検索は業務アシスタントを選ぶと business_view_id を送る", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await mockBusinessViews(page, [
    {
      id: "bv-1",
      name: "経理アシスタント",
      description: null,
      status: "ACTIVE",
      knowledge_base_count: 1,
      created_at: "2026-06-19T00:00:00Z",
      updated_at: "2026-06-19T00:00:00Z",
      archived_at: null,
    },
  ]);

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

  await page.getByRole("combobox", { name: /対象の業務アシスタント/ }).click();
  await page
    .getByRole("listbox", { name: /対象の業務アシスタント/ })
    .getByRole("option", { name: "経理アシスタント" })
    .click();
  await page.getByRole("textbox", { name: "RAG 検索" }).fill("経費精算の上限");
  await page.getByRole("button", { name: "検索" }).click();

  await expect.poll(() => searchPayload?.business_view_id).toBe("bv-1");
  await expect.poll(() => searchPayload?.knowledge_base_ids).toBeUndefined();
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 760 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`RAG 検索は業務アシスタントが無いと作成導線の空状態を出す (${viewport.name})`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockBusinessViews(page, []);

    await page.goto("/search");

    // 作成を促す空状態と CTA を表示する。
    await expect(page.getByText("業務アシスタントを作成してください")).toBeVisible();
    await expect(page.getByRole("button", { name: "業務アシスタントを作成" })).toBeVisible();

    // 検索入力・知識ベースピッカーは出さない(業務アシスタント一本化)。
    await expect(page.getByRole("textbox", { name: "RAG 検索" })).toHaveCount(0);
    await expect(page.getByText("知識ベース名で絞り込み")).toHaveCount(0);

    await expectNoPageOverflow(page);

    // CTA は業務アシスタント管理へ遷移する。
    await page.getByRole("button", { name: "業務アシスタントを作成" }).click();
    await expect(page).toHaveURL(/\/business-views$/);
  });
}

test("RAG 検索は業務アシスタント未選択だと必須エラーを出し送信しない", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await mockBusinessViews(page, [
    {
      id: "bv-1",
      name: "経理アシスタント",
      description: null,
      status: "ACTIVE",
      knowledge_base_count: 1,
      created_at: "2026-06-19T00:00:00Z",
      updated_at: "2026-06-19T00:00:00Z",
      archived_at: null,
    },
  ]);

  let searchCalled = false;
  await page.route("**/api/search/stream", async (route) => {
    searchCalled = true;
    await route.fulfill({
      status: 200,
      headers: { "content-type": "text/event-stream" },
      body: searchStreamBody(),
    });
  });

  await page.goto("/search");

  // 業務アシスタントを選ばずに検索すると必須エラー。
  await page.getByRole("textbox", { name: "RAG 検索" }).fill("経費精算の上限");
  await page.getByRole("button", { name: "検索" }).click();

  await expect(page.getByText("対象の業務アシスタントを選択してください。")).toBeVisible();
  expect(searchCalled).toBe(false);
});

interface BusinessViewSummaryFixture {
  id: string;
  name: string;
  description: string | null;
  status: string;
  knowledge_base_count: number;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
}

async function mockBusinessViews(
  page: Page,
  items: BusinessViewSummaryFixture[],
  onCreate?: (body: Record<string, unknown>) => void
) {
  await page.route("**/api/business-views**", async (route) => {
    const request = route.request();
    if (request.method() === "POST") {
      const body = request.postDataJSON() as Record<string, unknown>;
      onCreate?.(body);
      await route.fulfill({
        json: {
          data: {
            id: "bv-new",
            name: body.name,
            description: body.description ?? null,
            status: "ACTIVE",
            knowledge_base_count:
              (body.config as { knowledge_base_ids?: string[] })?.knowledge_base_ids?.length ?? 0,
            config: body.config,
            knowledge_bases: [],
            created_at: "2026-06-19T00:00:00Z",
            updated_at: "2026-06-19T00:00:00Z",
            archived_at: null,
          },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }
    await route.fulfill({
      json: {
        data: { items, total: items.length, limit: 50, offset: 0, has_next: false },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
}

async function mockKnowledgeBases(page: Page) {
  await page.route("**/api/knowledge-bases**", async (route) => {
    await route.fulfill({
      json: {
        data: {
          items: [
            {
              id: "kb-1",
              name: "社内規程",
              description: "経費・人事・情報管理",
              status: "ACTIVE",
              default_search_mode: "hybrid",
              document_count: 3,
              indexed_document_count: 3,
              error_document_count: 0,
              searchable_chunk_count: 16,
              created_at: "2026-06-15T00:00:00Z",
              updated_at: "2026-06-15T00:00:00Z",
              archived_at: null,
            },
          ],
          total: 1,
          limit: 50,
          offset: 0,
          has_next: false,
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
}

function searchStreamBody(): string {
  return [
    `event: metadata\ndata: ${JSON.stringify({
      trace_id: "trace-bv",
      elapsed_ms: 10,
      guardrail_warnings: [],
      diagnostics: { business_view_applied: "bv-1" },
    })}\n\n`,
    `event: delta\ndata: ${JSON.stringify({ text: "上限額を確認しました。" })}\n\n`,
    `event: citations\ndata: ${JSON.stringify([])}\n\n`,
    `event: done\ndata: ${JSON.stringify({ trace_id: "trace-bv" })}\n\n`,
  ].join("");
}
