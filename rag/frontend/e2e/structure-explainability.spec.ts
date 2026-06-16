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

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await page.route("**/api/auth/me", async (route) => {
    await route.fulfill({ json: authStatus });
  });
});

test("Dashboard で取込品質を確認できる", async ({ page }) => {
  await page.route("**/api/dashboard/summary", async (route) => {
    await route.fulfill({
      json: {
        data: dashboardSummary(),
        error_messages: [],
        warning_messages: [],
      },
    });
  });

  await page.goto("/dashboard");

  await expect(page.getByRole("heading", { name: "取込品質" })).toBeVisible();
  await expect(page.getByText("75%")).toBeVisible();
  await expect(page.getByText("3/4 文書")).toBeVisible();
  await expect(page.getByText("structure_v1")).toBeVisible();
  await expect(page.getByTitle("table")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("文書詳細で構造化抽出要素と raw text を確認できる", async ({ page }) => {
  await mockDocumentDetail(page);

  await page.goto("/documents/doc-1");

  await expect(page.getByRole("heading", { name: "抽出本文" })).toBeVisible();
  await expect(page.getByText("構造化要素")).toBeVisible();
  await expect(page.getByText("見出し")).toBeVisible();
  await expect(page.getByText("p.2")).toBeVisible();
  await expect(page.getByText("経費申請 > 料金表")).toBeVisible();
  await expect(page.getByText("| 交通費 | 1000 |")).toBeVisible();
  await expect(page.getByText("本文テキスト")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("文書詳細で所属知識ベースを更新できる", async ({ page }) => {
  const state = await mockDocumentDetail(page);

  await page.goto("/documents/doc-1");

  await expect(page.getByRole("heading", { name: "所属知識ベース" })).toBeVisible();
  await expect(page.getByLabel(/社内規程/)).toBeChecked();
  await page.getByLabel(/FAQ/).check();
  await page.getByRole("button", { name: "保存" }).click();

  await expect
    .poll(() => state.lastReplacePayload)
    .toEqual({ knowledge_base_ids: ["kb-1", "kb-2"] });
  await expect(page.getByText("所属知識ベースを保存しました。")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("検索引用で構造 metadata chip を確認できる", async ({ page }) => {
  let feedbackPayload: Record<string, unknown> | null = null;
  await page.route("**/api/search/stream", async (route) => {
    await route.fulfill({
      status: 200,
      headers: { "content-type": "text/event-stream" },
      body: searchStreamBody(),
    });
  });
  await page.route("**/api/search/citation-feedback", async (route) => {
    feedbackPayload = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      json: {
        data: {
          feedback_id: "feedback-1",
          trace_id: "trace-1",
          document_id: "doc-1",
          chunk_id: "doc-1:1",
          rating: "helpful",
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });

  await page.goto("/search");
  await page.getByLabel("RAG 検索").fill("料金表を確認");
  await page.getByRole("button", { name: "検索" }).click();

  await expect(page.getByRole("heading", { name: /引用/ })).toBeVisible();
  const citation = page.locator("li").filter({ hasText: "料金表の交通費は 1000 円です。" });
  await expect(page.getByText("p.2-3")).toBeVisible();
  await expect(citation.locator("dl").getByText("表", { exact: true })).toBeVisible();
  await expect(citation.getByText("経費申請 > 料金表")).toBeVisible();
  await expect(citation.getByText("structure_v1")).toBeVisible();
  await citation.getByRole("button", { name: "この引用は役に立った" }).click();
  await expect.poll(() => feedbackPayload).toEqual({
    trace_id: "trace-1",
    document_id: "doc-1",
    chunk_id: "doc-1:1",
    rating: "helpful",
    reason: null,
  });
  await expect(page.getByText("フィードバックを保存しました。")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

async function mockDocumentDetail(page: Page) {
  const catalog = [
    knowledgeBase("kb-1", "社内規程", 1),
    knowledgeBase("kb-2", "FAQ", 0),
  ];
  let membership: { id: string; name: string }[] = [{ id: "kb-1", name: "社内規程" }];
  const state: { lastReplacePayload: { knowledge_base_ids: string[] } | null } = {
    lastReplacePayload: null,
  };

  await page.route("**/api/knowledge-bases**", async (route) => {
    await route.fulfill({
      json: {
        data: {
          items: catalog,
          total: catalog.length,
          limit: 50,
          offset: 0,
          has_next: false,
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await page.route("**/api/documents/doc-1/knowledge-bases", async (route) => {
    if (route.request().method() === "PUT") {
      state.lastReplacePayload = route.request().postDataJSON() as {
        knowledge_base_ids: string[];
      };
      membership = catalog
        .filter((knowledgeBase) =>
          state.lastReplacePayload?.knowledge_base_ids.includes(knowledgeBase.id)
        )
        .map(({ id, name }) => ({ id, name }));
    }

    await route.fulfill({
      json: {
        data: membership,
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await page.route("**/api/documents/doc-1", async (route) => {
    await route.fulfill({
      json: {
        data: {
          id: "doc-1",
          file_name: "policy.txt",
          status: "INDEXED",
          category_name: null,
          content_type: "text/plain",
          file_size_bytes: 120,
          content_sha256: "a".repeat(64),
          duplicate_of_document_id: null,
          knowledge_bases: [{ id: "kb-1", name: "社内規程" }],
          uploaded_at: "2026-06-14T00:00:00Z",
          indexed_at: "2026-06-14T00:01:00Z",
          object_storage_path: "local://policy.txt",
          error_message: null,
          extraction: {
            raw_text: "# 経費申請\n| 項目 | 金額 |",
            document_type: "規程",
            confidence: 0.92,
            warnings: [],
            elements: [
              {
                kind: "title",
                text: "# 経費申請",
                order: 0,
                page_number: 1,
                section_path: ["経費申請"],
                confidence: 0.95,
              },
              {
                kind: "table",
                text: "| 項目 | 金額 |\n| 交通費 | 1000 |",
                order: 1,
                page_number: 2,
                section_path: ["経費申請", "料金表"],
                confidence: 0.88,
              },
            ],
          },
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await page.route("**/api/documents/doc-1/content", async (route) => {
    await route.fulfill({
      status: 200,
      headers: { "content-type": "text/plain" },
      body: "# 経費申請\n| 項目 | 金額 |",
    });
  });
  return state;
}

function knowledgeBase(id: string, name: string, documentCount: number) {
  return {
    id,
    name,
    description: null,
    status: "ACTIVE",
    default_search_mode: "hybrid",
    document_count: documentCount,
    indexed_document_count: documentCount,
    error_document_count: 0,
    searchable_chunk_count: documentCount * 2,
    created_at: "2026-06-14T00:00:00Z",
    updated_at: "2026-06-14T00:00:00Z",
    archived_at: null,
  };
}

function dashboardSummary() {
  return {
    stats: {
      total_uploads: 4,
      uploads_this_month: 4,
      total_indexed: 3,
      indexed_this_month: 3,
      searchable_rows: 8,
    },
    ingestion_quality: {
      document_count: 4,
      structured_document_count: 3,
      element_count: 18,
      table_count: 2,
      list_count: 4,
      page_count: 6,
      chunk_profile_counts: { structure_v1: 7, text_v1: 1 },
      content_kind_counts: { text: 4, table: 2, list: 2 },
    },
    recent_activities: [],
    system: {
      status: "online",
      version: "0.1.0",
      searchable_rows: 8,
      checks: { local_storage: "ok" },
    },
  };
}

function searchStreamBody(): string {
  const citation = {
    document_id: "doc-1",
    chunk_id: "doc-1:1",
    text: "料金表の交通費は 1000 円です。",
    score: 0.91,
    rerank_score: 0.96,
    file_name: "policy.txt",
    category_name: null,
    metadata: {
      page_start: 2,
      page_end: 3,
      content_kind: "table",
      section_title: "料金表",
      section_path: "経費申請 > 料金表",
      chunk_profile: "structure_v1",
    },
  };
  return [
    `event: metadata\ndata: ${JSON.stringify({
      trace_id: "trace-1",
      elapsed_ms: 12,
      guardrail_warnings: [],
      diagnostics: {},
    })}\n\n`,
    `event: delta\ndata: ${JSON.stringify({ text: "料金表を確認しました。" })}\n\n`,
    `event: citations\ndata: ${JSON.stringify([citation])}\n\n`,
    `event: done\ndata: ${JSON.stringify({ trace_id: "trace-1" })}\n\n`,
  ].join("");
}

async function expectNoHorizontalOverflow(page: Page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth
    )
  ).toBe(true);
}
