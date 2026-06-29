import { expect, type Page, test } from "@playwright/test";
import { expectNoPageOverflow, mockDatabaseReady } from "./_helpers";

const authStatus = {
  data: { mode: "local", auth_required: false, authenticated: true, user: null, expires_at: null },
  error_messages: [],
  warning_messages: [],
};

const documentDetail = {
  id: "doc-1",
  file_name: "policy.pdf",
  status: "INDEXED",
  category_name: null,
  content_type: "application/pdf",
  file_size_bytes: 1024,
  content_sha256: "abc",
  duplicate_of_document_id: null,
  uploaded_at: "2026-06-15T00:00:00Z",
  indexed_at: "2026-06-15T00:05:00Z",
  knowledge_bases: [{ id: "kb-1", name: "社内規程" }],
  object_storage_path: "indexed/doc-1.pdf",
  extraction: {},
  error_message: null,
};

function ok(json: unknown) {
  return { data: json, error_messages: [], warning_messages: [] };
}

async function mockWorkspace(
  page: Page,
  drift: boolean,
  parserDrift = false,
  buildConfigurations?: unknown[],
  knowledgeBases = [{ id: "kb-1", name: "社内規程" }]
) {
  await page.route("**/api/knowledge-bases**", (route) =>
    route.fulfill({
      json: ok({ items: knowledgeBases, total: knowledgeBases.length, limit: 100, offset: 0, has_next: false }),
    })
  );
  await page.route("**/api/documents/doc-1/ingestion-config", (route) =>
    route.fulfill({
      json: ok({
        document_id: "doc-1",
        is_indexed: true,
        owning_knowledge_base: { id: "kb-1", name: "社内規程" },
        effective_preprocess_profile: "office_to_pdf",
        effective_chunking_strategy: "page_level",
        effective_parser_adapter_backend: parserDrift ? "mineru" : "docling",
        observed_chunking_strategy: drift ? "structure_aware" : "page_level",
        observed_parser_backend: parserDrift ? "enterprise_ai_pdf_layout" : "local",
        chunking_drift: drift,
        parser_drift: parserDrift,
        config_drift: drift || parserDrift,
        ...(buildConfigurations ? { build_configurations: buildConfigurations } : {}),
      }),
    })
  );
  let enqueued = false;
  await page.route("**/api/documents/doc-1/ingestion-jobs**", (route) => {
    if (route.request().method() === "POST") {
      enqueued = true;
      route.fulfill({
        json: ok({
          id: "job-1",
          document_id: "doc-1",
          status: "QUEUED",
          parser_profile: "pdf",
          quality_warnings: [],
          skip_reason: null,
          error_message: null,
          attempt_count: 0,
          max_attempts: 3,
          queued_at: "2026-06-15T00:10:00Z",
          started_at: null,
          finished_at: null,
        }),
      });
      return;
    }
    route.fulfill({ json: ok([]) });
  });
  await page.route("**/api/documents/doc-1/chunks", (route) => route.fulfill({ json: ok([]) }));
  await page.route("**/api/documents/doc-1/ingestion-segments", (route) =>
    route.fulfill({ json: ok([]) })
  );
  await page.route("**/api/documents/doc-1/knowledge-bases", (route) =>
    route.fulfill({ json: ok(knowledgeBases) })
  );
  await page.route("**/api/documents/doc-1/extraction-export**", (route) =>
    route.fulfill({
      json: ok({
        document_id: "doc-1",
        file_name: "policy.pdf",
        format: "markdown",
        content_type: "text/markdown",
        content: "",
        payload: {},
        chunks: [],
        parser_backend: "local",
        parser_profile: "pdf",
        page_count: 0,
        element_count: 0,
        table_count: 0,
        asset_count: 0,
      }),
    })
  );
  await page.route("**/api/documents/doc-1/content", (route) =>
    route.fulfill({ status: 204, body: "" })
  );
  await page.route("**/api/documents/doc-1", (route) =>
    route.fulfill({ json: ok({ ...documentDetail, knowledge_bases: knowledgeBases }) })
  );
  return () => enqueued;
}

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await page.route("**/api/auth/me", (route) => route.fulfill({ json: authStatus }));
});

test("取込設定ドリフト時にバナーを表示し、再取込を実行できる", async ({ page }) => {
  const wasEnqueued = await mockWorkspace(page, true);

  await page.goto("/documents/doc-1");

  const banner = page.getByRole("status").filter({ hasText: "取込設定が更新されています" });
  await expect(banner).toBeVisible();
  await expect(banner).toContainText("page_level");

  await banner.getByRole("button", { name: "現在の設定で再取込" }).click();
  await expect(page.getByText("再取込を開始しました。")).toBeVisible();
  expect(wasEnqueued()).toBe(true);
});

test("文書解析ドリフト時に MinerU への差分を表示する", async ({ page }) => {
  await mockWorkspace(page, false, true);

  await page.goto("/documents/doc-1");

  const banner = page.getByRole("status").filter({ hasText: "取込設定が更新されています" });
  await expect(banner).toBeVisible();
  await expect(banner).toContainText("文書解析");
  await expect(banner).toContainText("PDF レイアウト解析");
  await expect(banner).toContainText("MinerU");
});

test("ドリフトが無ければバナーを表示しない", async ({ page }) => {
  await mockWorkspace(page, false);

  await page.goto("/documents/doc-1");

  await expect(page.getByRole("heading", { name: "原本プレビュー" })).toBeVisible();
  await expect(page.getByText("取込設定が更新されています")).toHaveCount(0);

  // 旧 API 応答でも構築設定を文脈内に表示する。
  const buildConfig = page.getByRole("region", { name: "ナレッジベース別の構築設定" });
  await expect(buildConfig).toBeVisible();
  await expect(buildConfig).toContainText("Office→PDF");
  await expect(buildConfig).toContainText("ページ単位");
  await expect(buildConfig.getByRole("link")).toHaveCount(0);
});

for (const viewport of [
  { name: "desktop", width: 1440, height: 1000 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`KB ごとに異なる構築設定を分けて表示する (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const knowledgeBases = [
      { id: "kb-1", name: "社内規程" },
      { id: "kb-2", name: "製品 FAQ" },
    ];
    await mockWorkspace(
      page,
      false,
      false,
      [
        buildConfigGroup({
          id: "a",
          knowledgeBases: [knowledgeBases[0]],
          chunkSize: 800,
          graphProfile: "off",
          reviewTarget: true,
          state: "serving",
        }),
        buildConfigGroup({
          id: "b",
          knowledgeBases: [knowledgeBases[1]],
          chunkSize: 1200,
          graphProfile: "entities",
          state: "update_required",
        }),
      ],
      knowledgeBases
    );

    await page.goto("/documents/doc-1");

    const region = page.getByRole("region", { name: "ナレッジベース別の構築設定" });
    await expect(region).toContainText("2 KB・2種類");
    await expect(region.getByRole("link", { name: "社内規程" })).toHaveAttribute(
      "href",
      "/knowledge-bases/kb-1"
    );
    await expect(region.getByRole("link", { name: "製品 FAQ" })).toHaveAttribute(
      "href",
      "/knowledge-bases/kb-2"
    );
    await expect(region).toContainText("サイズ 800");
    await expect(region).toContainText("サイズ 1200");
    await expect(region).toContainText("配信中");
    await expect(region).toContainText("更新が必要");
    await expect(region).toContainText("確認対象");
    await expectNoPageOverflow(page);
  });
}

test("同じ構築設定を使う KB は共有グループにまとめる", async ({ page }) => {
  const knowledgeBases = [
    { id: "kb-1", name: "社内規程" },
    { id: "kb-2", name: "製品 FAQ" },
  ];
  await mockWorkspace(
    page,
    false,
    false,
    [buildConfigGroup({ id: "shared", knowledgeBases, chunkSize: 800, state: "planned" })],
    knowledgeBases
  );

  await page.goto("/documents/doc-1");

  const region = page.getByRole("region", { name: "ナレッジベース別の構築設定" });
  await expect(region).toContainText("2 KB・1種類");
  await expect(region.getByText("共有", { exact: true })).toBeVisible();
  await expect(region.getByRole("listitem")).toHaveCount(1);
});

test("構築設定の読込中と取得失敗を文書画面内で通知する", async ({ page }) => {
  await mockWorkspace(page, false);
  await page.unroute("**/api/documents/doc-1/ingestion-config");
  await page.route("**/api/documents/doc-1/ingestion-config", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 700));
    await route.fulfill({ status: 500, json: { detail: "構築設定を取得できませんでした。" } });
  });

  await page.goto("/documents/doc-1");

  await expect(
    page.getByRole("status", { name: "構築設定を読み込んでいます…" })
  ).toBeVisible();
  const region = page.getByRole("region", { name: "ナレッジベース別の構築設定" });
  await expect(region.getByText("構築設定を取得できません", { exact: true })).toBeVisible();
  await expect(region.getByRole("button", { name: "再試行" })).toBeVisible();
});

function buildConfigGroup({
  id,
  knowledgeBases,
  chunkSize,
  graphProfile = "off",
  reviewTarget = false,
  state,
}: {
  id: string;
  knowledgeBases: Array<{ id: string; name: string }>;
  chunkSize: number;
  graphProfile?: "off" | "entities";
  reviewTarget?: boolean;
  state: "planned" | "serving" | "update_required";
}) {
  const layer = (requested = false) => ({
    layer_id: requested ? `layer-${id}` : null,
    requested,
    status: requested ? "planned_only" : "not_requested",
    reason: null,
  });
  return {
    knowledge_bases: knowledgeBases,
    effective_config: {
      preprocess_profile: "office_to_pdf",
      parser_adapter_backend: "docling",
      parser_docling_enabled: true,
      parser_marker_enabled: false,
      parser_unstructured_enabled: false,
      chunking_strategy: "page_level",
      chunk_size: chunkSize,
      chunk_overlap: 80,
      chunk_child_size: 200,
      chunk_sentence_window_size: 3,
      chunk_min_chars: 80,
      graph_profile: graphProfile,
      field_extraction_enabled: false,
      asset_summary_enabled: false,
      navigation_summary_enabled: false,
      auto_parse_after_preprocess_enabled: true,
      auto_chunk_after_extract_enabled: true,
      auto_index_after_chunk_enabled: true,
    },
    is_review_target: reviewTarget,
    extraction_recipe_id: `er-${id}`,
    chunk_set_id: `cs-${id}`,
    state,
    reason: state === "update_required" ? "現在の構築設定で再取込が必要です。" : null,
    chunk_count: state === "serving" ? 8 : 0,
    vector_count: state === "serving" ? 8 : 0,
    serving_knowledge_base_count: state === "serving" ? knowledgeBases.length : 0,
    layer_statuses: {
      metadata: layer(),
      graph: layer(graphProfile !== "off"),
      navigation: layer(),
    },
  };
}
