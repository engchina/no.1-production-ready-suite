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

const kbSummary = {
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
};

// chunking_strategy だけ上書き、それ以外は継承(= 個別設定 1 / 10 項目)。
const adapterConfig = {
  version: 1,
  ingestion: {
    preprocess_profile: null,
    parser_adapter_backend: null,
    parser_docling_enabled: null,
    parser_marker_enabled: null,
    parser_unstructured_enabled: null,
    chunking_strategy: "page_level",
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
  },
  query: {
    retrieval_strategy: null,
    post_retrieval_pipeline: null,
    generation_profile: null,
    guardrail_policy: null,
    vector_index_profile: null,
    evaluation_suite: null,
  },
};

// 継承フィールドはグローバル既定で解決済み(継承行に実効値を表示するため)。
const effectiveAdapterConfig = {
  version: 1,
  ingestion: {
    preprocess_profile: "office_to_pdf",
    parser_adapter_backend: "docling",
    parser_docling_enabled: true,
    parser_marker_enabled: false,
    parser_unstructured_enabled: false,
    chunking_strategy: "page_level",
    chunk_size: 1000,
    chunk_overlap: 100,
    chunk_child_size: 300,
    chunk_sentence_window_size: 3,
    chunk_min_chars: 50,
    graph_profile: "off",
    field_extraction_enabled: false,
    asset_summary_enabled: false,
    navigation_summary_enabled: false,
    auto_parse_after_preprocess_enabled: false,
    auto_chunk_after_extract_enabled: false,
    auto_index_after_chunk_enabled: false,
  },
  query: {
    retrieval_strategy: "hybrid_rrf",
    post_retrieval_pipeline: "custom",
    generation_profile: "grounded_concise",
    guardrail_policy: "standard",
    vector_index_profile: "balanced",
    evaluation_suite: "request_only",
  },
};

const kbDetail = {
  ...kbSummary,
  retrieval_config: {},
  adapter_config: adapterConfig,
  effective_adapter_config: effectiveAdapterConfig,
};

async function mockKnowledgeBasePage(page: Page): Promise<void> {
  await page.route("**/api/documents**", (route) =>
    route.fulfill({ json: ok({ items: [], total: 0, limit: 50, offset: 0, has_next: false }) })
  );
  await page.route("**/api/knowledge-bases**", (route) =>
    route.fulfill({
      json: ok({ items: [kbSummary], total: 1, limit: 20, offset: 0, has_next: false }),
    })
  );
  // 詳細・文書サブルートは generic より後に登録して優先させる。
  await page.route("**/api/knowledge-bases/kb-1/documents**", (route) =>
    route.fulfill({ json: ok({ items: [], total: 0, limit: 50, offset: 0, has_next: false }) })
  );
  await page.route("**/api/knowledge-bases/kb-1", (route) =>
    route.fulfill({ json: ok(kbDetail) })
  );
}

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await page.route("**/api/auth/me", (route) => route.fulfill({ json: authStatus }));
});

test("KB 詳細ページの構築設定が上書き件数と継承の解決値を表示する", async ({ page }) => {
  await mockKnowledgeBasePage(page);

  await page.goto("/knowledge-bases/kb-1");

  // 上書き件数サマリ(7 項目中 1 項目が個別設定)。
  await expect(page.getByText("個別設定 1 / 10 項目")).toBeVisible();

  // 継承行は「実際に効く値(グローバル既定の解決値)」を表示する。
  await expect(
    page.getByText("グローバル設定に従う: Office→PDF")
  ).toBeVisible();
  await expect(page.getByText("根拠重視・簡潔")).toHaveCount(0);

  // 構築フロー(read-only 地図)がナレッジ構築に使う段だけを表示する。
  const ribbon = page.getByRole("region", { name: "構築フロー" });
  await expect(ribbon).toBeVisible();
  await expect(ribbon.getByText("現在の構築設定")).toBeVisible();
  await expect(ribbon.getByText("検索・回答設定")).toHaveCount(0);
  // 上書き段(文書分割=page_level)はリボンに上書きバッジ + 値が出る。
  await expect(ribbon.getByText("ページ単位")).toBeVisible();
  // two-phase auto 進行フィールドでも「上書き」バッジが出るため最初の 1 件で可視を確認する。
  await expect(ribbon.getByText("上書き", { exact: true }).first()).toBeVisible();

  // 取込側の高度軸(関係情報 / メタデータ抽出 / 図表要約 / ナビ要約)も表示。
  await expect(ribbon.getByText("関係情報の構築")).toBeVisible();
  await expect(ribbon.getByText("構築しない")).toBeVisible();
});

test("一覧から KB 名リンクで詳細ページへ遷移できる", async ({ page }) => {
  await mockKnowledgeBasePage(page);

  await page.goto("/knowledge-bases");

  // 一覧では右サイドバーに構築設定を出さない(詳細ページへ移設済み)。
  await expect(page.getByText("個別設定 1 / 10 項目")).toHaveCount(0);

  await page.getByRole("link", { name: "社内規程" }).click();

  await expect(page).toHaveURL(/\/knowledge-bases\/kb-1$/);
  await expect(page.getByText("個別設定 1 / 10 項目")).toBeVisible();
});

test("構築パイプライン図(高度な診断)を展開して工程ノードを表示する", async ({ page }) => {
  await mockKnowledgeBasePage(page);

  await page.goto("/knowledge-bases/kb-1");

  await page.getByRole("button", { name: "パイプライン図を表示" }).click();
  const canvas = page.getByRole("region", { name: "構築パイプライン図(高度な診断)" });
  await expect(canvas).toBeVisible();
  await expect(canvas.getByText("ファイル準備")).toBeVisible();
  await expect(canvas.getByText("文書分割")).toBeVisible();
  await expect(canvas.getByText("検索インデックス")).toBeVisible();
});
