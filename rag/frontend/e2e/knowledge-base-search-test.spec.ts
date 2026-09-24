import { expect, type Page, test } from "@playwright/test";
import {
  expectMainScrollEndsAtContent,
  expectNoPageOverflow,
  mockDatabaseReady,
} from "./_helpers";

const authStatus = {
  data: { mode: "local", auth_required: false, authenticated: true, user: null, expires_at: null },
  error_messages: [],
  warning_messages: [],
};

function ok(json: unknown) {
  return { data: json, error_messages: [], warning_messages: [] };
}

const adapterConfig = {
  version: 1,
  ingestion: {
    preprocess_profile: null,
    parser_adapter_backend: null,
    parser_docling_enabled: null,
    parser_marker_enabled: null,
    parser_unstructured_enabled: null,
    chunking_strategy: null,
    chunk_size: null,
    chunk_overlap: null,
    chunk_child_size: null,
    chunk_min_chars: null,
    chunk_context_header_enabled: null,
    graph_profile: null,
    field_extraction_enabled: null,
    asset_summary_enabled: null,
    navigation_summary_enabled: null,
    auto_parse_after_preprocess_enabled: null,
    auto_chunk_after_extract_enabled: null,
    auto_index_after_chunk_enabled: null,
  },
  query: {
    retrieval_strategy: null,
    post_retrieval_pipeline: null,
    generation_profile: null,
    guardrail_policy: null,
    evaluation_suite: null,
  },
};

function kbDetail(indexedDocumentCount: number) {
  return {
    id: "kb-1",
    name: "社内規程",
    description: "就業規則",
    status: "ACTIVE",
    default_search_mode: "hybrid",
    document_count: indexedDocumentCount,
    indexed_document_count: indexedDocumentCount,
    error_document_count: 0,
    searchable_chunk_count: indexedDocumentCount * 10,
    created_at: "2026-06-15T00:00:00Z",
    updated_at: "2026-06-15T00:00:00Z",
    archived_at: null,
    retrieval_config: {},
    adapter_config: adapterConfig,
    effective_adapter_config: adapterConfig,
  };
}

// SSE 応答(stage→metadata→citations→delta→done)。streamSearch は \n\n 区切りで解析する。
function searchStreamBody(fileName = "policy.pdf", citationCount = 1) {
  const citations = Array.from({ length: citationCount }, (_, index) => ({
    document_id: `doc-${index + 1}`,
    chunk_id: `doc-${index + 1}:cs_1:0`,
    text: index === 0 ? "就業規則の根拠テキスト" : `補足の根拠テキスト ${index + 1}`,
    score: 0.91,
    rerank_score: 0.82,
    file_name: index === 0 ? fileName : `policy-${index + 1}.pdf`,
    category_name: null,
    metadata: {},
  }));
  return [
    'event: stage\ndata: {"trace_id":"trace-1","stage":"retrieval","outcome":"success","elapsed_ms":12,"attributes":{}}',
    'event: metadata\ndata: {"trace_id":"trace-1","elapsed_ms":120,"guardrail_warnings":[],"diagnostics":{}}',
    `event: citations\ndata: ${JSON.stringify(citations)}`,
    'event: delta\ndata: {"text":"これはテスト回答です。"}',
    'event: done\ndata: {"trace_id":"trace-1"}',
    "",
  ].join("\n\n");
}

async function mockKbPage(page: Page, indexedDocumentCount: number): Promise<void> {
  await page.route("**/api/documents**", (route) =>
    route.fulfill({ json: ok({ items: [], total: 0, limit: 50, offset: 0, has_next: false }) })
  );
  // 上の wildcard より後に登録し、より具体的な /recipes を優先させる(Playwright は後勝ち)。
  await page.route("**/api/documents/doc-1/recipes", (route) =>
    route.fulfill({ json: ok([]) })
  );
  await page.route("**/api/knowledge-bases**", (route) =>
    route.fulfill({
      json: ok({ items: [kbDetail(indexedDocumentCount)], total: 1, limit: 20, offset: 0, has_next: false }),
    })
  );
  await page.route("**/api/knowledge-bases/kb-1/documents**", (route) =>
    route.fulfill({ json: ok({ items: [], total: 0, limit: 50, offset: 0, has_next: false }) })
  );
  await page.route("**/api/knowledge-bases/kb-1", (route) =>
    route.fulfill({ json: ok(kbDetail(indexedDocumentCount)) })
  );
}

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await page.route("**/api/auth/me", (route) => route.fulfill({ json: authStatus }));
});

test("KB 詳細の検索テストで業務ビュー無しに回答と引用を確認できる", async ({ page }) => {
  await mockKbPage(page, 1);
  let streamRequestBody: Record<string, unknown> | null = null;
  await page.route("**/api/search/stream", (route) => {
    streamRequestBody = JSON.parse(route.request().postData() ?? "{}");
    route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: searchStreamBody(),
    });
  });

  await page.goto("/knowledge-bases/kb-1");

  await expect(page.getByRole("heading", { name: "このナレッジで検索テスト" })).toBeVisible();

  await page.getByPlaceholder("この知識ベースに質問してみる…").fill("有給休暇の付与日数は？");
  await page.getByRole("button", { name: "検索テスト" }).click();

  // 回答と引用(原本ファイル名)が表示される。
  await expect(page.getByText("これはテスト回答です。")).toBeVisible();
  await expect(page.getByText("policy.pdf")).toBeVisible();
  await expect(page.getByRole("meter", { name: /取得スコア/ })).toHaveCount(0);
  await expect(page.getByRole("meter", { name: "Rerank スコア: 0.820" })).toBeVisible();

  // request は単一 KB scope を明示し、業務ビューは渡さない。
  expect(streamRequestBody).toMatchObject({ knowledge_base_ids: ["kb-1"] });
  expect(streamRequestBody).not.toHaveProperty("business_view_ids");

  // 引用プレビューを画面に留まったままドロワー(native dialog)で確認・全画面導線も保持。
  await page.getByRole("button", { name: "プレビュー" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("link", { name: "全画面で開く" })).toHaveAttribute(
    "href",
    /\/documents\/doc-1/
  );
  await dialog.getByRole("button", { name: "閉じる" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);

  await expectNoPageOverflow(page);
});

test("KB 検索テストの引用が内部スクロールしてもページ末尾に空白を作らない", async ({ page }) => {
  await mockKbPage(page, 1);
  await page.route("**/api/search/stream", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: searchStreamBody("policy.pdf", 8),
    })
  );

  await page.goto("/knowledge-bases/kb-1");
  await page.getByPlaceholder("この知識ベースに質問してみる…").fill("有給休暇の付与日数は？");
  await page.getByRole("button", { name: "検索テスト" }).click();

  const citationList = page.locator("ul.bounded-scroll-area-lg");
  await expect(citationList.locator(":scope > li")).toHaveCount(8);
  await expect
    .poll(() => citationList.evaluate((element) => element.scrollHeight > element.clientHeight))
    .toBe(true);
  await expectMainScrollEndsAtContent(page);
});

test("索引済み文書が無い KB は検索テストを促す空状態を出す", async ({ page }) => {
  await mockKbPage(page, 0);

  await page.goto("/knowledge-bases/kb-1");

  await expect(page.getByText("索引済みの文書がありません。")).toBeVisible();
  // 索引前は入力欄を出さない。
  await expect(page.getByPlaceholder("この知識ベースに質問してみる…")).toHaveCount(0);

  await expectNoPageOverflow(page);
});

test("Office 引用プレビューの降格表示では原本をダウンロードできる", async ({ page }) => {
  await mockKbPage(page, 1);
  await page.route("**/api/documents/doc-1", (route) =>
    route.fulfill({ json: ok({ preprocess_artifact: null }) })
  );
  await page.route("**/api/search/stream", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: searchStreamBody("policy.docx"),
    })
  );

  await page.goto("/knowledge-bases/kb-1");
  await page.getByPlaceholder("この知識ベースに質問してみる…").fill("有給休暇の付与日数は？");
  await page.getByRole("button", { name: "検索テスト" }).click();
  await page.getByRole("button", { name: "プレビュー" }).click();

  const dialog = page.getByRole("dialog");
  await expect(
    dialog.getByText("Office 原本はブラウザーで直接表示できません", { exact: false })
  ).toBeVisible();
  await expect(
    dialog.getByRole("link", { name: "ファイルをダウンロード", exact: true })
  ).toHaveAttribute("href", /\/api\/documents\/doc-1\/content\?disposition=attachment$/);
});
