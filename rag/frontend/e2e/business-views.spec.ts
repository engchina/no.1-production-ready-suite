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
  test(`業務ビュー管理は作成フォームを表示し横崩れしない (${viewport.name})`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockBusinessViews(page, []);

    await page.goto("/business-views");

    await expect(
      page.getByRole("heading", { name: "業務ビュー (Business View)" })
    ).toBeVisible();
    await expect(page.getByRole("heading", { name: "業務ビューを作成" })).toBeVisible();
    await expect(page.getByLabel("名前", { exact: true })).toBeVisible();
    await expect(page.getByText("参照する知識ベース", { exact: false }).first()).toBeVisible();
    // 知識ベースはコンボボックスを開くと候補として現れる。
    await page.getByRole("combobox", { name: "参照する知識ベース" }).click();
    await expect(page.getByRole("option", { name: /社内規程/ })).toBeVisible();
    const settings = page.locator("fieldset").filter({ hasText: "検索・回答設定" });
    await expect(settings.getByRole("heading", { level: 3 })).toHaveText([
      "検索方法",
      "検索オプション",
      "根拠確認",
      "回答スタイル",
      "回答プロンプト",
      "安全チェック",
      "品質評価",
    ]);
    await expect(settings.getByRole("heading", { name: "検索インデックス" })).toHaveCount(0);
    // 継承 chip: セレクト5行 + 検索オプションの三値トグル4行。
    await expect(settings.getByRole("button", { name: "グローバル既定を継承" })).toHaveCount(9);
    await expect(settings.getByRole("button", { name: "業務ビューで上書き" })).toHaveCount(5);
    await expect(page.getByLabel("回答の役割・口調")).toBeVisible();
    await expectNoPageOverflow(page);
  });
}

test("業務ビューを作成すると参照 KB と方針を含めて POST する", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  let createBody: Record<string, unknown> | null = null;
  await mockBusinessViews(page, [], (body) => {
    createBody = body;
  });

  await page.goto("/business-views");

  await page.getByRole("combobox", { name: "参照する知識ベース" }).click();
  await page.getByRole("option", { name: /社内規程/ }).click();
  await page.getByRole("combobox", { name: "参照する知識ベース" }).press("Escape");
  await page.getByLabel("名前", { exact: true }).fill("経理ビュー");
  const retrievalSetting = page.getByRole("heading", { name: "検索方法", level: 3 }).locator("..");
  const override = retrievalSetting.getByRole("button", { name: "業務ビューで上書き" });
  await override.click();
  await expect(override).toHaveAttribute("aria-pressed", "true");
  await retrievalSetting.getByRole("combobox", { name: "検索方法" }).click();
  await page.getByRole("option", { name: "キーワード" }).click();
  await page.getByLabel("回答の役割・口調").fill("あなたは経理規程に詳しい回答担当です。");
  await page.getByRole("button", { name: "作成する" }).click();

  await expect
    .poll(() => (createBody?.config as { knowledge_base_ids?: string[] })?.knowledge_base_ids)
    .toEqual(["kb-1"]);
  expect(createBody?.name).toBe("経理ビュー");
  expect((createBody?.config as { system_prompt?: string })?.system_prompt).toContain(
    "経理規程"
  );
  expect(
    (createBody?.config as { query?: { retrieval_strategy?: string } })?.query?.retrieval_strategy
  ).toBe("keyword");
  expect(
    "vector_index_profile" in
      ((createBody?.config as { query?: Record<string, unknown> })?.query ?? {})
  ).toBe(false);
  // 3 層モデルでは配信モード UI を持たず、常に全 recipe を融合する。
  expect((createBody?.config as { serving_mode?: string })?.serving_mode).toBe("fused");
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 760 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`DEFAULT は参照 KB と名前を固定し設定だけ保存できる (${viewport.name})`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    let updateBody: Record<string, unknown> | null = null;
    await mockDefaultBusinessView(page, (body) => {
      updateBody = body;
    });

    await page.goto("/business-views");

    const card = page.getByRole("listitem").filter({ hasText: "DEFAULT" });
    await expect(card.getByRole("button", { name: "DEFAULT はアーカイブできません" })).toBeDisabled();
    await card.getByRole("button", { name: "編集" }).click();

    await expect(page.getByLabel("名前", { exact: true })).toHaveAttribute("readonly", "");
    await expect(page.getByText("DEFAULT の名前は変更できません。")).toBeVisible();
    await expect(page.getByRole("combobox", { name: "参照する知識ベース" })).toBeDisabled();
    await expect(page.getByText(/DEFAULT 知識ベースだけを参照します/)).toBeVisible();

    await page.getByLabel("説明", { exact: true }).fill("全社共通の検索設定");
    await page.getByLabel("回答の役割・口調").fill("全社共通の回答担当です。");
    await page.getByRole("button", { name: "保存する" }).click();

    await expect.poll(() => updateBody?.name).toBeUndefined();
    await expect
      .poll(() => (updateBody?.config as { knowledge_base_ids?: string[] })?.knowledge_base_ids)
      .toEqual(["kb-default"]);
    expect((updateBody?.config as { system_prompt?: string })?.system_prompt).toContain("全社共通");
    expect(updateBody?.description).toBe("全社共通の検索設定");
    await expectNoPageOverflow(page);
  });
}

test("業務ビュー作成では DEFAULT を予約名として拒否する", async ({ page }) => {
  await mockBusinessViews(page, []);
  await page.goto("/business-views");

  await page.getByLabel("名前", { exact: true }).fill(" default ");
  await page.getByLabel("名前", { exact: true }).blur();

  await expect(page.getByText("DEFAULT は予約名のため使用できません。")).toBeVisible();
});

test("RAG 検索は複数業務ビューを選ぶと business_view_ids を送る", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await mockBusinessViews(page, [
    {
      id: "bv-1",
      name: "経理ビュー",
      description: null,
      status: "ACTIVE",
      knowledge_base_count: 1,
      created_at: "2026-06-19T00:00:00Z",
      updated_at: "2026-06-19T00:00:00Z",
      archived_at: null,
    },
    {
      id: "bv-2",
      name: "人事ビュー",
      description: null,
      status: "ACTIVE",
      knowledge_base_count: 2,
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

  await page.getByRole("combobox", { name: /対象の業務ビュー/ }).click();
  const businessViewList = page.getByRole("listbox", { name: /対象の業務ビュー/ });
  await businessViewList.getByRole("option", { name: /経理ビュー/ }).click();
  await businessViewList.getByRole("option", { name: /人事ビュー/ }).click();
  await page.getByRole("textbox", { name: "RAG 検索" }).fill("経費精算の上限");
  await page.getByRole("button", { name: "検索", exact: true }).click();

  await expect.poll(() => searchPayload?.business_view_ids).toEqual(["bv-1", "bv-2"]);
  await expect.poll(() => searchPayload?.knowledge_base_ids).toBeUndefined();
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 760 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`RAG 検索は業務ビューが無いと作成導線の空状態を出す (${viewport.name})`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockBusinessViews(page, []);

    await page.goto("/search");

    // 作成を促す空状態と CTA を表示する。
    await expect(page.getByText("業務ビューを作成してください")).toBeVisible();
    await expect(page.getByRole("button", { name: "業務ビューを作成" })).toBeVisible();

    // 検索入力・知識ベースピッカーは出さない(業務ビュー一本化)。
    await expect(page.getByRole("textbox", { name: "RAG 検索" })).toHaveCount(0);
    await expect(page.getByText("知識ベース名で絞り込み")).toHaveCount(0);

    await expectNoPageOverflow(page);

    // CTA は業務ビュー管理へ遷移する。
    await page.getByRole("button", { name: "業務ビューを作成" }).click();
    await expect(page).toHaveURL(/\/business-views$/);
  });
}

test("RAG 検索は業務ビュー未選択だと必須エラーを出し送信しない", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await mockBusinessViews(page, [
    {
      id: "bv-1",
      name: "経理ビュー",
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

  // 業務ビューを選ばずに検索すると必須エラー。
  await page.getByRole("textbox", { name: "RAG 検索" }).fill("経費精算の上限");
  await page.getByRole("button", { name: "検索", exact: true }).click();

  await expect(page.getByText("対象の業務ビューを選択してください。")).toBeVisible();
  expect(searchCalled).toBe(false);
});

test("RAG 検索は DEFAULT を候補表示するが自動選択しない", async ({ page }) => {
  await mockBusinessViews(page, [
    {
      id: "bv-default",
      name: "DEFAULT",
      description: null,
      status: "ACTIVE",
      knowledge_base_count: 1,
      created_at: "2026-06-30T00:00:00Z",
      updated_at: "2026-06-30T00:00:00Z",
      archived_at: null,
    },
  ]);
  await page.goto("/search");

  await page.getByRole("combobox", { name: /対象の業務ビュー/ }).click();
  await expect(page.getByRole("option", { name: /DEFAULT/ })).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByRole("textbox", { name: "RAG 検索" }).fill("全社規程");
  await page.getByRole("button", { name: "検索", exact: true }).click();

  await expect(page.getByText("対象の業務ビューを選択してください。")).toBeVisible();
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
              id: "kb-default",
              name: "DEFAULT",
              description: null,
              status: "ACTIVE",
              default_search_mode: "hybrid",
              document_count: 0,
              indexed_document_count: 0,
              error_document_count: 0,
              searchable_chunk_count: 0,
              created_at: "2026-06-30T00:00:00Z",
              updated_at: "2026-06-30T00:00:00Z",
              archived_at: null,
            },
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

async function mockDefaultBusinessView(
  page: Page,
  onUpdate: (body: Record<string, unknown>) => void
) {
  const summary: BusinessViewSummaryFixture = {
    id: "bv-default",
    name: "DEFAULT",
    description: null,
    status: "ACTIVE",
    knowledge_base_count: 1,
    created_at: "2026-06-30T00:00:00Z",
    updated_at: "2026-06-30T00:00:00Z",
    archived_at: null,
  };
  const config = {
    version: 1,
    knowledge_base_ids: ["kb-default"],
    query: {
      retrieval_strategy: null,
      post_retrieval_pipeline: null,
      generation_profile: null,
      guardrail_policy: null,
      evaluation_suite: null,
    },
    system_prompt: null,
    default_language: null,
    serving_mode: "single",
  };

  await page.route("**/api/business-views**", async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (request.method() === "PATCH") {
      const body = request.postDataJSON() as Record<string, unknown>;
      onUpdate(body);
      await route.fulfill({
        json: {
          data: { ...summary, description: body.description, config: body.config, knowledge_bases: [] },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }
    if (pathname.endsWith("/bv-default")) {
      await route.fulfill({
        json: {
          data: {
            ...summary,
            config,
            knowledge_bases: [{ id: "kb-default", name: "DEFAULT" }],
          },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }
    await route.fulfill({
      json: {
        data: { items: [summary], total: 1, limit: 50, offset: 0, has_next: false },
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
