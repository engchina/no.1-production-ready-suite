import { expect, type Page, test } from "@playwright/test";
import { expectNoPageOverflow, mockDatabaseReady } from "./_helpers";

// 1x1 透明 PNG。`<img>` で実際に描画できる有効な data URI。
const PNG_PIXEL =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";
// 分割で生じた prefix 無し base64 断片の模擬。
const BASE64_FRAGMENT = "A".repeat(300);

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
  await expect(page.getByText("構造メトリクス")).toBeVisible();
  await expect(page.getByText("図", { exact: true })).toBeVisible();
  await expect(page.getByText("数式", { exact: true })).toBeVisible();
  await expect(page.getByText("品質ヘルス")).toBeVisible();
  await expect(page.getByText("ページ網羅率", { exact: true })).toBeVisible();
  await expect(page.getByText("88%", { exact: true })).toBeVisible();
  await expect(page.getByText("フォールバック", { exact: true })).toBeVisible();
  const segmentArtifactMetric = page.getByText("Segment artifact 再抽出", { exact: true });
  await segmentArtifactMetric.scrollIntoViewIfNeeded();
  await expect(segmentArtifactMetric).toBeVisible();
  const parserBackend = page.getByText("解析エンジン", { exact: true });
  await parserBackend.scrollIntoViewIfNeeded();
  await expect(parserBackend).toBeVisible();
  await expect(page.getByText("ローカル partition", { exact: true })).toBeVisible();
  const chunkProfile = page.getByText("structure_v1", { exact: true });
  await chunkProfile.scrollIntoViewIfNeeded();
  await expect(chunkProfile).toBeVisible();
  const tableKind = page.getByTitle("table");
  await tableKind.scrollIntoViewIfNeeded();
  await expect(tableKind).toBeVisible();
  await expectNoHorizontalOverflow(page);

  await page.setViewportSize({ width: 375, height: 900 });
  await expect(page.getByRole("heading", { name: "取込品質" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("文書詳細で構造化抽出要素と raw text を確認できる", async ({ page }) => {
  await mockDocumentDetail(page);

  await page.goto("/documents/doc-1");

  // 抽出本文タブが既定で開いており、その tabpanel に内容が表示される。
  const extractionPanel = page.getByRole("tabpanel");
  await expect(extractionPanel).toBeVisible();
  await expect(extractionPanel.getByText("構造化要素")).toBeVisible();
  await expect(extractionPanel.getByText("見出し")).toBeVisible();
  await expect(extractionPanel.getByRole("button", { name: /表 table p\.2/ })).toBeVisible();
  await expect(extractionPanel.getByText("経費申請 > 料金表")).toBeVisible();
  await expect(extractionPanel.getByText("| 交通費 | 1000 |")).toBeVisible();
  await expect(extractionPanel.getByText("表セル")).toBeVisible();
  await expect(extractionPanel.getByRole("button", { name: /料金表 B2 1000/ })).toBeVisible();
  await expect(extractionPanel.getByText("本文テキスト")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("文書詳細で埋め込み base64 を畳んで本文を読みやすく表示する", async ({ page }) => {
  await mockDocumentDetail(page, {
    extraction: {
      raw_text: `本文の冒頭。 ${PNG_PIXEL} 本文の続き。`,
      document_type: "規程",
      confidence: 0.9,
      warnings: [],
      elements: [
        {
          kind: "figure",
          text: `図1 ${PNG_PIXEL}`,
          order: 0,
          element_id: "el-0000",
          content_kind: "figure",
          source_parser: "oci_genai_vision",
          page_number: 1,
          section_path: [],
          confidence: 0.8,
        },
      ],
      pages: [{ page_number: 1, element_ids: ["el-0000"] }],
      tables: [],
      assets: [],
      parser_artifacts: {},
    },
    chunks: [
      {
        document_id: "doc-1",
        chunk_id: "doc-1:0",
        chunk_index: 0,
        text: BASE64_FRAGMENT,
        page_start: 1,
        page_end: 1,
        bbox: null,
        section_path: null,
        content_kind: "figure",
        chunk_group_id: "grp-1",
        source_parser: "oci_genai_vision",
        element_ids: ["el-0000"],
        metadata: {},
      },
    ],
  });

  await page.goto("/documents/doc-1");

  // 既定の抽出本文タブ(tabpanel)。
  const extractionPanel = page.getByRole("tabpanel");
  await expect(extractionPanel).toBeVisible();

  // base64 の生文字列は本文テキストとして現れない。
  await expect(page.getByText(/iVBORw0KGgo/)).toHaveCount(0);
  await expect(page.getByText(BASE64_FRAGMENT)).toHaveCount(0);
  // 画像はサムネイル(alt 付き <img>)で描画される。
  await expect(
    extractionPanel.getByRole("img", { name: "抽出された埋め込み画像" }).first()
  ).toBeVisible();
  // 読める本文は残る。
  await expect(extractionPanel.getByText("図1")).toBeVisible();

  // Chunk タブへ切替えると、base64 断片はチップに畳まれて表示される。
  await page.getByRole("tab", { name: /Chunk \/ Citation/ }).click();
  const chunkPanel = page.getByRole("tabpanel");
  await expect(chunkPanel.getByText(/画像データ/)).toBeVisible();

  await expectNoHorizontalOverflow(page);

  await page.setViewportSize({ width: 375, height: 900 });
  await expect(page.getByRole("tabpanel")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("文書詳細で所属知識ベースを更新できる", async ({ page }) => {
  const state = await mockDocumentDetail(page);

  await page.goto("/documents/doc-1");

  await expect(page.getByRole("heading", { name: "所属知識ベース" })).toBeVisible();
  // 既存の所属はチップで可視化される。
  await expect(page.getByLabel("社内規程 を選択から外す")).toBeVisible();
  // FAQ は空(0 文書)なので「空のKBを隠す」を解除してから選ぶ。
  const kbCombo = page.getByRole("combobox", { name: "所属先" });
  await kbCombo.click();
  await page.getByRole("checkbox", { name: "空のKBを隠す" }).uncheck();
  await page.getByRole("option", { name: /FAQ/ }).click();
  await kbCombo.press("Escape");
  await page.getByRole("button", { name: "保存" }).click();

  await expect
    .poll(() => state.lastReplacePayload)
    .toEqual({ knowledge_base_ids: ["kb-1", "kb-2"] });
  await expect(page.getByText("所属知識ベースを保存しました。")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("検索引用で構造 metadata chip を確認できる", async ({ page }) => {
  let feedbackPayload: Record<string, unknown> | null = null;
  await mockDocumentDetail(page);
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
  await page.getByRole("combobox", { name: /対象の業務ビュー/ }).click();
  await page
    .getByRole("listbox", { name: /対象の業務ビュー/ })
    .getByRole("option", { name: /経理ビュー/ })
    .click();
  await page.getByRole("textbox", { name: "RAG 検索" }).fill("料金表を確認");
  await page.getByRole("button", { name: "検索", exact: true }).click();

  await expect(page.getByRole("heading", { name: /引用/ })).toBeVisible();
  // 詳細メトリクス(適応展開/依存昇格)は「診断」ディスクロージャ内にあるため展開する。
  await page.getByRole("button", { name: "診断" }).click();
  const executionMetrics = page.getByLabel("検索実行");
  await expect(executionMetrics.locator("div").filter({ hasText: "適応展開" })).toContainText("2");
  await expect(executionMetrics.locator("div").filter({ hasText: "依存昇格" })).toContainText("1");
  const citation = page.locator("li").filter({ hasText: "料金表の交通費は 1000 円です。" });
  await expect(page.getByText("p.2-3")).toBeVisible();
  await expect(citation.locator("dl").getByText("表", { exact: true })).toBeVisible();
  await expect(citation.getByText("経費申請 > 料金表")).toBeVisible();
  await expect(citation.getByText("structure_v1")).toBeVisible();
  const previewLink = citation.getByRole("link", { name: "policy.txt の引用位置を開く" });
  await expect(previewLink).toHaveAttribute(
    "href",
    /\/documents\/doc-1\?chunk_id=doc-1%3A1&page=2&element_id=tbl-1&cell_ref=B2&formula_cell_ref=B2/
  );
  await citation.getByRole("button", { name: "この引用は役に立った" }).click();
  await expect.poll(() => feedbackPayload).toEqual({
    trace_id: "trace-1",
    document_id: "doc-1",
    chunk_id: "doc-1:1",
    rating: "helpful",
    reason: null,
  });
  await expect(page.getByText("フィードバックを保存しました。")).toBeVisible();
  await previewLink.click();
  await expect(page).toHaveURL(
    /\/documents\/doc-1\?chunk_id=doc-1%3A1&page=2&element_id=tbl-1&cell_ref=B2&formula_cell_ref=B2/
  );
  // セル指定付き deep-link では抽出本文タブが初期表示され、対象セルがフォーカスされる。
  const linkedCellButton = page.getByRole("button", { name: /料金表 B2 1000/ });
  await expect(linkedCellButton).toHaveAttribute("aria-pressed", "true");
  await expect(linkedCellButton).toBeFocused();
  // Chunk タブへ切替えると紐づく chunk が選択されている。
  await page.getByRole("tab", { name: /Chunk \/ Citation/ }).click();
  const linkedChunkButton = page
    .getByRole("tabpanel")
    .getByRole("button", { name: /料金表の交通費/ });
  await expect(linkedChunkButton).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText(/位置: p\.2 \/ bbox x=0\.0% y=0\.0% w=50\.0% h=20\.0%/)).toBeVisible();
  const bboxOverlay = page.getByTestId("bbox-preview-overlay");
  await expect(bboxOverlay).toBeVisible();
  await expect(bboxOverlay).toHaveAttribute("data-bbox-unit", "absolute");
  await expectNoHorizontalOverflow(page);
});

async function mockDocumentDetail(
  page: Page,
  overrides?: { extraction?: Record<string, unknown>; chunks?: unknown[] }
) {
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
  await page.route("**/api/business-views**", async (route) => {
    // 検索ページは業務ビュー選択が前提のため、最低 1 件を返す。
    await route.fulfill({
      json: {
        data: {
          items: [
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
          extraction: overrides?.extraction ?? {
            raw_text: "# 経費申請\n| 項目 | 金額 |",
            document_type: "規程",
            confidence: 0.92,
            warnings: [],
            elements: [
              {
                kind: "title",
                text: "# 経費申請",
                order: 0,
                element_id: "el-0000",
                content_kind: "text",
                source_parser: "local_text_structure",
                page_number: 1,
                bbox: [0, 0, 100, 20],
                section_path: ["経費申請"],
                confidence: 0.95,
              },
              {
                kind: "table",
                text: "| 項目 | 金額 |\n| 交通費 | 1000 |",
                order: 1,
                element_id: "tbl-1",
                content_kind: "table",
                source_parser: "local_text_structure",
                page_number: 2,
                bbox: [0, 0, 100, 40],
                section_path: ["経費申請", "料金表"],
                confidence: 0.88,
              },
            ],
            pages: [
              { page_number: 1, width: 612, height: 792, element_ids: ["el-0000"] },
              { page_number: 2, element_ids: ["tbl-1"] },
            ],
            tables: [
              {
                table_id: "table-1",
                element_id: "tbl-1",
                page_number: 2,
                caption: "料金表",
                cells: [
                  {
                    row: 0,
                    col: 0,
                    text: "項目",
                    row_span: 1,
                    col_span: 1,
                  },
                  {
                    row: 0,
                    col: 1,
                    text: "金額",
                    row_span: 1,
                    col_span: 1,
                  },
                  {
                    row: 1,
                    col: 0,
                    text: "交通費",
                    row_span: 1,
                    col_span: 1,
                  },
                  {
                    row: 1,
                    col: 1,
                    text: "1000",
                    row_span: 1,
                    col_span: 1,
                    bbox: [0, 0, 306, 0, 306, 158.4, 0, 158.4],
                    metadata: {
                      formula_cell_ref: "B2",
                      bbox_coordinate_mode: "xyxy",
                      bbox_unit: "absolute",
                      page_width: 612,
                      page_height: 792,
                    },
                  },
                ],
              },
            ],
            assets: [],
            parser_artifacts: { parser_backend: "local_partition" },
          },
          source_profile: {
            original_file_name: "policy.txt",
            sanitized_file_name: "policy.txt",
            extension: ".txt",
            content_type: "text/plain",
            inferred_content_type: "text/plain",
            file_size_bytes: 120,
            content_sha256: "a".repeat(64),
            modality: "text",
            parser_profile: "local_text_structure",
            parser_backend: "local_partition",
            parser_version: "local_partition_v1",
            preview_kind: "text",
            text_charset: "utf-8",
            duplicate_of_document_id: null,
            unsupported_reason: null,
            quality_status: "ready",
            quality_warnings: [],
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
  await page.route("**/api/documents/doc-1/chunks", async (route) => {
    await route.fulfill({
      json: {
        data: overrides?.chunks ?? [
          {
            document_id: "doc-1",
            chunk_id: "doc-1:0",
            chunk_index: 0,
            text: "# 経費申請",
            page_start: 1,
            page_end: 1,
            bbox: [0, 0, 100, 20],
            section_path: "経費申請",
            content_kind: "text",
            chunk_group_id: "grp-1",
            source_parser: "local_text_structure",
            element_ids: ["el-0000"],
            metadata: { chunk_profile: "structure_v1", element_ids: "el-0000" },
          },
          {
            document_id: "doc-1",
            chunk_id: "doc-1:1",
            chunk_index: 1,
            text: "料金表の交通費は 1000 円です。",
            page_start: 2,
            page_end: 2,
            bbox: null,
            section_path: "経費申請 > 料金表",
            content_kind: "table",
            chunk_group_id: "grp-2",
            source_parser: "local_text_structure",
            element_ids: ["tbl-1"],
            metadata: {
              chunk_profile: "structure_v1",
              element_ids: "tbl-1",
              bbox_json: "[0,0,612,316.8]",
              bbox_coordinate_mode: "xyxy",
              bbox_unit: "absolute",
              page_width: 612,
              page_height: 792,
            },
          },
        ],
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await page.route("**/api/documents/doc-1/ingestion-segments", async (route) => {
    await route.fulfill({
      json: {
        data: [],
        error_messages: [],
        warning_messages: [],
      },
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
      figure_count: 3,
      formula_count: 2,
      list_count: 4,
      page_count: 6,
      low_confidence_count: 5,
      fallback_document_count: 1,
      failed_segment_document_count: 1,
      segment_artifact_cache_miss_document_count: 1,
      long_document_count: 1,
      average_page_coverage: 0.875,
      risk_counts: { low: 2, medium: 1, high: 1 },
      parser_profile_counts: { enterprise_ai_pdf_layout: 2, local_text_structure: 1 },
      parser_backend_counts: { local_partition: 2, enterprise_ai: 1 },
      warning_counts: { parser_fallback_used: 1, failed_segments: 1 },
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
      element_ids: "tbl-1",
      formula_cell_refs:
        '{"table_id":"tbl-1","cells":[{"metadata":{"formula_cell_ref":"B2"}}]}',
    },
  };
  return [
    `event: metadata\ndata: ${JSON.stringify({
      trace_id: "trace-1",
      elapsed_ms: 12,
      guardrail_warnings: [],
      diagnostics: {
        retrieved_count: 6,
        reranked_count: 3,
        citation_count: 1,
        context_adaptive_expanded_count: 2,
        context_dependency_promoted_count: 1,
        context_group_expanded_count: 0,
        context_expanded_count: 0,
        context_compressed_count: 0,
      },
    })}\n\n`,
    `event: delta\ndata: ${JSON.stringify({ text: "料金表を確認しました。" })}\n\n`,
    `event: citations\ndata: ${JSON.stringify([citation])}\n\n`,
    `event: done\ndata: ${JSON.stringify({ trace_id: "trace-1" })}\n\n`,
  ].join("");
}

async function expectNoHorizontalOverflow(page: Page) {
  // documentElement と main の双方を検査する共通ヘルパーへ委譲(_helpers.ts)。
  await expectNoPageOverflow(page);
}
