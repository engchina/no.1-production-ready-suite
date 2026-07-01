import { expect, type Page, test } from "@playwright/test";
import { mockDatabaseReady } from "./_helpers";

const authStatus = {
  data: { mode: "local", auth_required: false, authenticated: true, user: null, expires_at: null },
  error_messages: [],
  warning_messages: [],
};

function ok(json: unknown) {
  return { data: json, error_messages: [], warning_messages: [] };
}

const nullIngestion = {
  preprocess_profile: null,
  parser_adapter_backend: null,
  parser_docling_enabled: null,
  parser_marker_enabled: null,
  parser_unstructured_enabled: null,
  chunking_strategy: null,
  chunk_size: null,
  chunk_overlap: null,
  chunk_child_size: null,
  chunk_sentence_window_size: null,
  chunk_min_chars: null,
  graph_profile: null,
  field_extraction_enabled: null,
  asset_summary_enabled: null,
  navigation_summary_enabled: null,
  auto_parse_after_preprocess_enabled: null,
  auto_chunk_after_extract_enabled: null,
  auto_index_after_chunk_enabled: null,
};
const nullQuery = {
  retrieval_strategy: null,
  post_retrieval_pipeline: null,
  generation_profile: null,
  guardrail_policy: null,
  evaluation_suite: null,
};
const kbDetail = {
  id: "kb-1",
  name: "社内規程",
  description: "就業規則",
  status: "ACTIVE",
  default_search_mode: "hybrid",
  document_count: 1,
  indexed_document_count: 1,
  error_document_count: 0,
  searchable_chunk_count: 10,
  created_at: "2026-06-15T00:00:00Z",
  updated_at: "2026-06-15T00:00:00Z",
  archived_at: null,
  retrieval_config: {},
  adapter_config: { version: 1, ingestion: nullIngestion, query: nullQuery },
  effective_adapter_config: {
    version: 1,
    ingestion: {
      ...nullIngestion,
      preprocess_profile: "office_to_pdf",
      parser_adapter_backend: "docling",
      chunking_strategy: "page_level",
      graph_profile: "entities",
      field_extraction_enabled: false,
      asset_summary_enabled: false,
      navigation_summary_enabled: false,
    },
    query: nullQuery,
  },
};

async function mockKb(page: Page, graph: unknown): Promise<void> {
  await page.route("**/api/documents**", (route) =>
    route.fulfill({ json: ok({ items: [], total: 0, limit: 50, offset: 0, has_next: false }) })
  );
  // generic を先に、具体ルートを後に登録(Playwright は後勝ち)。
  await page.route("**/api/knowledge-bases**", (route) =>
    route.fulfill({ json: ok({ items: [kbDetail], total: 1, limit: 20, offset: 0, has_next: false }) })
  );
  await page.route("**/api/knowledge-bases/kb-1/documents**", (route) =>
    route.fulfill({ json: ok({ items: [], total: 0, limit: 50, offset: 0, has_next: false }) })
  );
  await page.route("**/api/knowledge-bases/kb-1/graph**", (route) =>
    route.fulfill({ json: ok(graph) })
  );
  await page.route("**/api/knowledge-bases/kb-1", (route) => route.fulfill({ json: ok(kbDetail) }));
}

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await page.route("**/api/auth/me", (route) => route.fulfill({ json: authStatus }));
});

test("関係情報グラフを展開して entity ノードを表示する", async ({ page }) => {
  await mockKb(page, {
    status: "ok",
    nodes: [
      { id: "e1", name: "就業規則", type: "concept", confidence: 0.9 },
      { id: "e2", name: "有給休暇", type: "concept", confidence: 0.8 },
    ],
    edges: [{ id: "r1", source: "e1", target: "e2", type: "relates_to", confidence: 0.7 }],
    truncated: false,
  });

  await page.goto("/knowledge-bases/kb-1");
  await page.getByRole("button", { name: "関係情報グラフを表示" }).click();

  const graph = page.getByRole("region", { name: "関係情報グラフ" });
  await expect(graph).toBeVisible();
  await expect(graph.getByText("就業規則")).toBeVisible();
  await expect(graph.getByText("有給休暇")).toBeVisible();

  const main = page.locator("main");
  await graph.scrollIntoViewIfNeeded();
  const mainScrollTop = await main.evaluate((element) => element.scrollTop);
  await graph.hover();
  await page.mouse.wheel(0, -600);
  await expect.poll(() => main.evaluate((element) => element.scrollTop)).toBeLessThan(mainScrollTop);

  await page.getByRole("button", { name: "パイプライン図を表示" }).click();
  const pipeline = page.getByRole("region", { name: "構築パイプライン図(高度な診断)" });
  await pipeline.scrollIntoViewIfNeeded();
  const mainScrollTopAtPipeline = await main.evaluate((element) => element.scrollTop);
  await pipeline.hover();
  await page.mouse.wheel(0, -600);
  await expect.poll(() => main.evaluate((element) => element.scrollTop)).toBeLessThan(
    mainScrollTopAtPipeline
  );
});

test("関係情報が無い KB は空状態を出す", async ({ page }) => {
  await mockKb(page, { status: "empty", nodes: [], edges: [], truncated: false });

  await page.goto("/knowledge-bases/kb-1");
  await page.getByRole("button", { name: "関係情報グラフを表示" }).click();

  await expect(page.getByText("関係情報がまだありません。")).toBeVisible();
});
