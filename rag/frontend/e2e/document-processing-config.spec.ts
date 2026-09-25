import { expect, type Page, test } from "@playwright/test";

import type { DocumentProcessingConfig, DocumentRecipeStep } from "../src/lib/api";
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
  extraction: {
    raw_text: "経費申請の規程",
    elements: [
      {
        kind: "text",
        text: "経費申請の規程",
        order: 0,
        element_id: "e0",
        metadata: {},
      },
    ],
  },
  error_message: null,
};

function ok(data: unknown) {
  return { data, error_messages: [], warning_messages: [] };
}

function config(): DocumentProcessingConfig {
  return {
    preprocess_profile: null,
    parser_adapter_backend: null,
    parser_docling_enabled: null,
    parser_marker_enabled: null,
    parser_unstructured_enabled: null,
    parser_unlimited_ocr_enabled: null,
    parser_mineru_enabled: null,
    parser_dots_ocr_enabled: null,
    parser_glm_ocr_enabled: null,
    chunking_strategy: null,
    chunk_size: 800,
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
  };
}

const effectiveBase: DocumentProcessingConfig = {
  ...config(),
  preprocess_profile: "office_to_pdf",
  parser_adapter_backend: "docling",
  chunking_strategy: "page_level",
  graph_profile: "off",
  field_extraction_enabled: false,
  asset_summary_enabled: false,
  navigation_summary_enabled: false,
  auto_parse_after_preprocess_enabled: true,
  auto_chunk_after_extract_enabled: true,
  auto_index_after_chunk_enabled: true,
};

function recipeSteps(status: string): DocumentRecipeStep[] {
  const phases = ["PREPROCESS", "EXTRACT", "CHUNK", "INDEX"] as const;
  const completedCount: Record<string, number> = {
    UPLOADED: 0,
    REVIEW: 2,
    INDEXED: 4,
  };
  return phases.map((phase, index) => ({
    phase,
    status: index < (completedCount[status] ?? 0) ? "SUCCEEDED" : "PENDING",
    started_at: null,
    finished_at: null,
    error_message: null,
  }));
}

async function mockWorkspace(
  page: Page,
  options: {
    documentStatus?: string;
    putFails?: boolean;
    previewFails?: boolean;
    recipeCount?: number;
  } = {}
) {
  const status = options.documentStatus ?? "INDEXED";
  let processing = config();
  let saved: DocumentProcessingConfig | null = null;
  let ingestionPosts = 0;
  let previewPosts = 0;
  let previewPayload: unknown = null;
  const recipeResponse = (slotNo = 1) => {
    const effective = { ...effectiveBase };
    for (const [key, value] of Object.entries(processing)) {
      if (value !== null) Object.assign(effective, { [key]: value });
    }
    return {
      recipe_id: `recipe-${slotNo}`,
      document_id: "doc-1",
      slot_no: slotNo,
      status,
      failed_phase: null,
      processing_config: processing,
      effective_processing_config: effective,
      preprocess_artifact: null,
      active_extraction_recipe_id:
        status === "INDEXED" || status === "REVIEW" ? `er-recipe-${slotNo}-r1` : null,
      active_chunk_set_id: status === "INDEXED" ? `chunk-set-recipe-${slotNo}` : null,
      chunk_count: status === "INDEXED" ? 2 : 0,
      vector_count: status === "INDEXED" ? 2 : 0,
      config_revision: 1,
      materialized_revision: status === "INDEXED" ? 1 : null,
      searchable: status === "INDEXED",
      needs_reprocessing: false,
      error_message: null,
      steps: recipeSteps(status),
      created_at: "2026-06-15T00:00:00Z",
      updated_at: "2026-06-15T00:00:20Z",
      started_at: null,
      finished_at: status === "INDEXED" ? "2026-06-15T00:00:20Z" : null,
    };
  };

  await page.route("**/api/knowledge-bases**", (route) =>
    route.fulfill({
      json: ok({
        items: [{ id: "kb-1", name: "社内規程" }],
        total: 1,
        limit: 100,
        offset: 0,
        has_next: false,
      }),
    })
  );
  await page.route("**/api/documents/doc-1/recipes", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        json: ok(
          Array.from({ length: options.recipeCount ?? 1 }, (_, index) =>
            recipeResponse(index + 1)
          )
        ),
      });
    }
    return route.fulfill({ status: 404, json: { data: null, error_messages: [], warning_messages: [] } });
  });
  await page.route("**/api/documents/doc-1/recipes/recipe-1", (route) => {
    if (route.request().method() === "PUT") {
      if (options.putFails) {
        return route.fulfill({
          status: 409,
          json: {
            data: null,
            error_messages: ["取込ジョブの実行中は設定を変更できません。"],
            warning_messages: [],
          },
        });
      }
      saved = route.request().postDataJSON() as DocumentProcessingConfig;
      processing = saved;
    }
    return route.fulfill({ json: ok(recipeResponse()) });
  });
  await page.route("**/api/documents/doc-1/recipes/recipe-1/ingestion-jobs**", (route) => {
    if (route.request().method() === "POST") ingestionPosts += 1;
    route.fulfill({ json: ok([]) });
  });
  await page.route("**/api/documents/doc-1/recipes/recipe-1/chunks", (route) =>
    route.fulfill({ json: ok([]) })
  );
  await page.route("**/api/documents/doc-1/recipes/recipe-1/chunk-preview", (route) => {
    previewPosts += 1;
    previewPayload = route.request().postDataJSON();
    if (options.previewFails) {
      return route.fulfill({
        status: 503,
        json: {
          data: null,
          error_messages: ["分割サービスに接続できませんでした。"],
          warning_messages: [],
        },
      });
    }
    return route.fulfill({
      json: ok({
        chunks: [
          {
            document_id: "doc-1",
            chunk_id: "preview:recipe-1:0",
            chunk_index: 0,
            text: "経費申請は部門長の承認後、経理部が確認します。",
            page_start: 1,
            page_end: 1,
            bbox: null,
            section_path: "第1章 > 経費申請",
            content_kind: "text",
            chunk_group_id: null,
            source_parser: "docling",
            element_ids: ["e0"],
            metadata: { context_header: "policy.pdf > 第1章 > 経費申請" },
          },
        ],
        stats: {
          chunk_count: 1,
          min_chars: 24,
          average_chars: 24,
          max_chars: 24,
          overflow_count: 0,
          embedding_overflow_count: 0,
        },
        warnings: [],
      }),
    });
  });
  await page.route("**/api/documents/doc-1/recipes/recipe-1/extraction-export**", (route) =>
    route.fulfill({
      json: ok({
        document_id: "doc-1",
        file_name: "policy.pdf",
        format: "markdown",
        content_type: "text/markdown",
        content: "",
        payload: documentDetail.extraction,
        chunks: [],
        parser_backend: "docling",
        parser_profile: "docling",
        page_count: 0,
        element_count: 0,
        table_count: 0,
        asset_count: 0,
      }),
    })
  );
  await page.route("**/api/documents/doc-1/ingestion-jobs**", (route) =>
    route.fulfill({ json: ok([]) })
  );
  await page.route("**/api/documents/doc-1/chunk-sets", (route) => route.fulfill({ json: ok([]) }));
  await page.route("**/api/documents/doc-1/ingestion-segments", (route) =>
    route.fulfill({ json: ok([]) })
  );
  await page.route("**/api/documents/doc-1/knowledge-bases", (route) =>
    route.fulfill({ json: ok([{ id: "kb-1", name: "社内規程" }]) })
  );
  await page.route("**/api/documents/doc-1/content**", (route) =>
    route.fulfill({ status: 204, body: "" })
  );
  await page.route("**/api/documents/doc-1/recipes/recipe-1/content**", (route) =>
    route.fulfill({ status: 204, body: "" })
  );
  await page.route("**/api/documents/doc-1", (route) =>
    route.fulfill({
      json: ok({ ...documentDetail, status }),
    })
  );

  return {
    saved: () => saved,
    ingestionPosts: () => ingestionPosts,
    previewPosts: () => previewPosts,
    previewPayload: () => previewPayload,
  };
}

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await page.route("**/api/auth/me", (route) => route.fulfill({ json: authStatus }));
});

test("文書処理設定を保存し、手動再処理を案内する", async ({ page }) => {
  const state = await mockWorkspace(page);
  await page.goto("/documents/doc-1");

  const panel = page.getByRole("region", { name: "処理レシピ" });
  await expect(panel).toContainText("Office→PDF");
  await expect(panel).toContainText("Docling");
  for (const label of [
    "ファイル準備後に抽出へ進む",
    "抽出後に Chunk 作成へ進む",
    "Chunk 後に Embedding / 索引へ進む",
  ]) {
    await expect(panel.getByText(label, { exact: true }).locator("../..")).toContainText("有効");
  }
  await panel.getByRole("button", { name: "処理設定を編集" }).click();

  await panel.getByRole("group", { name: "文書解析" }).getByText("上書き").click();
  await panel.getByRole("combobox", { name: "文書解析" }).click();
  await page.getByRole("option", { name: "MinerU" }).click();
  await panel.getByRole("button", { name: "構築設定を保存" }).click();

  await expect(page.getByText(/この文書の処理設定を保存しました/)).toBeVisible();
  expect(state.saved()).toMatchObject({ parser_adapter_backend: "mineru", chunk_size: 800 });
  expect(state.ingestionPosts()).toBe(0);
  await expectNoPageOverflow(page);
});

test("レシピ比較で空の引用を理由付きの状態として表示する", async ({ page }) => {
  await mockWorkspace(page, { recipeCount: 2 });
  const searchRequests: Array<Record<string, unknown>> = [];
  await page.route("**/api/search", (route) => {
    searchRequests.push(route.request().postDataJSON() as Record<string, unknown>);
    return route.fulfill({
      json: ok({
        answer: "取得候補の関連度が十分でないため、回答に使える根拠がありませんでした。",
        citations: [],
        trace_id: "trace-no-results",
        guardrail_warnings: ["回答に使える根拠がありませんでした。"],
        elapsed_ms: 12,
        diagnostics: {},
      }),
    });
  });
  await page.goto("/documents/doc-1");

  await page.getByRole("button", { name: "検索結果を横並びで比較" }).click();
  await page.getByLabel("比較用の検索クエリ").fill("承認条件を教えて");
  await page.getByRole("button", { name: "比較", exact: true }).click();

  await expect(page.getByText("一致する根拠が見つかりませんでした。")).toHaveCount(2);
  await expect(
    page.getByText("取得候補の関連度が十分でないため、回答に使える根拠がありませんでした。")
  ).toHaveCount(2);
  expect(
    searchRequests
      .map((request) => (request.filters as Record<string, string>).chunk_set_id)
      .sort()
  ).toEqual(["chunk-set-recipe-1", "chunk-set-recipe-2"]);
  await expectNoPageOverflow(page);
});

test("レシピ比較の引用も共有 score layout で表示する", async ({ page }) => {
  await mockWorkspace(page, { recipeCount: 2 });
  await page.route("**/api/search", (route) => {
    const request = route.request().postDataJSON() as {
      filters?: { chunk_set_id?: string };
    };
    const chunkSetId = request.filters?.chunk_set_id ?? "chunk-set";
    return route.fulfill({
      json: ok({
        answer: "承認条件の比較結果です。",
        citations: [
          {
            document_id: "doc-1",
            chunk_id: `doc-1:${chunkSetId}:0`,
            text: "申請は上長の承認後に精算します。",
            score: 0.048,
            rerank_score: 0.869,
            file_name: "policy.pdf",
            category_name: null,
            metadata: {},
          },
        ],
        trace_id: "trace-comparison",
        guardrail_warnings: [],
        elapsed_ms: 12,
        diagnostics: {},
      }),
    });
  });
  await page.goto("/documents/doc-1");

  await page.getByRole("button", { name: "検索結果を横並びで比較" }).click();
  await page.getByLabel("比較用の検索クエリ").fill("承認条件を教えて");
  await page.getByRole("button", { name: "比較", exact: true }).click();

  await expect(page.getByTestId("citation-score-panel")).toHaveCount(2);
  await expect(page.getByRole("meter", { name: /取得スコア/ })).toHaveCount(0);
  await expect(page.getByRole("meter", { name: "Rerank スコア: 0.869" })).toHaveCount(2);
  await expectNoPageOverflow(page);
});

test("保存失敗時は編集値を保持する", async ({ page }) => {
  await mockWorkspace(page, { putFails: true });
  await page.goto("/documents/doc-1");

  const panel = page.getByRole("region", { name: "処理レシピ" });
  await panel.getByRole("button", { name: "処理設定を編集" }).click();
  await panel.getByRole("group", { name: "文書解析" }).getByText("上書き").click();
  await panel.getByRole("combobox", { name: "文書解析" }).click();
  await page.getByRole("option", { name: "MinerU" }).click();
  await panel.getByRole("button", { name: "構築設定を保存" }).click();

  await expect(panel).toContainText("取込ジョブの実行中は設定を変更できません");
  await expect(panel.getByRole("combobox", { name: "文書解析" })).toContainText("MinerU");
});

test("派生レイヤー状態チップ・項目定義未設定の警告・抽出セクションを表示する", async ({ page }) => {
  await mockWorkspace(page);
  // 後着 route が優先される。派生 layer 状態と抽出 payload を上書きする。
  await page.route("**/api/documents/doc-1/chunk-sets", (route) =>
    route.fulfill({
      json: ok([
        {
          chunk_set_id: "chunk-set-recipe-1",
          extraction_recipe_id: "er-recipe-1-r1",
          extraction_status: "materialized",
          extraction_reason: null,
          status: "INDEXED",
          chunk_count: 2,
          vector_count: 2,
          is_serving: true,
          created_at: "2026-06-15T00:00:10Z",
          extraction_id: "ex-1",
          parser: "docling",
          preprocess: "office_to_pdf",
          knowledge_base_ids: ["kb-1"],
          serving_knowledge_base_ids: ["kb-1"],
          layer_statuses: {
            metadata: { layer_id: null, requested: false, status: "not_requested", reason: null },
            graph: {
              layer_id: "gl-1",
              requested: true,
              status: "planned_only",
              reason: "関係情報は構築計画に含まれていますが、まだ実体化していません。",
            },
            navigation: {
              layer_id: "nv-1",
              requested: true,
              status: "materialized",
              reason: "ナビゲーションは 2 件の章節として実体化済みです。",
            },
          },
        },
      ]),
    })
  );
  await page.route("**/api/documents/doc-1/recipes/recipe-1/extraction-export**", (route) =>
    route.fulfill({
      json: ok({
        document_id: "doc-1",
        file_name: "policy.pdf",
        format: "json",
        content_type: "application/json",
        content: "",
        payload: {
          raw_text: "経費申請の規程",
          elements: [{ kind: "text", text: "経費申請の規程", order: 0, element_id: "e0" }],
          navigation: [
            { section_id: "sec-1", title: "第1章 総則", depth: 0, page_start: 1, summary: "章の要約テキスト" },
            { section_id: "sec-2", title: "1.1 目的", depth: 1, parent_section_id: "sec-1" },
          ],
          fields: [
            { name: "請求書番号", value: "INV-1", value_type: "string", confidence: 0.9 },
          ],
          assets: [
            { asset_id: "fig-1", kind: "figure", alt_text: "承認フロー図", summary: "承認の流れを示す図", page_number: 2 },
          ],
        },
        chunks: [],
        parser_backend: "docling",
        parser_profile: "docling",
        page_count: 1,
        element_count: 1,
        table_count: 0,
        asset_count: 1,
      }),
    })
  );
  await page.route("**/api/settings/extraction-fields", (route) =>
    route.fulfill({ json: ok({ fields: [] }) })
  );

  await page.goto("/documents/doc-1");

  // レシピヘッダに派生 layer 状態チップ(not_requested は出さない)。
  const layerChips = page.getByTestId("recipe-layer-statuses");
  await expect(layerChips.getByTestId("recipe-layer-graph")).toContainText("計画のみ");
  await expect(layerChips.getByTestId("recipe-layer-navigation")).toContainText("構築済み");
  await expect(layerChips.getByTestId("recipe-layer-metadata")).toHaveCount(0);

  // 構造化要素タブに章節ナビ・図表要約・抽出項目の折りたたみセクション。
  await page.getByRole("tab", { name: "構造化要素" }).click();
  const navigationSection = page.getByTestId("extraction-navigation");
  await navigationSection.getByText("章節ナビゲーション").click();
  await expect(navigationSection).toContainText("第1章 総則");
  await expect(navigationSection).toContainText("章の要約テキスト");
  const assetSection = page.getByTestId("extraction-asset-summaries");
  await assetSection.getByText("図表の要約").click();
  await expect(assetSection).toContainText("承認の流れを示す図");
  const fieldsSection = page.getByTestId("extraction-fields");
  await fieldsSection.getByText("抽出項目").click();
  await expect(fieldsSection).toContainText("請求書番号");
  await expect(fieldsSection).toContainText("INV-1");

  // 項目抽出を上書きで有効にすると、スキーマ未設定の警告を出す。
  const panel = page.getByRole("region", { name: "処理レシピ" });
  await panel.getByRole("button", { name: "処理設定を編集" }).click();
  const fieldGroup = panel
    .getByText("メタデータ/項目抽出", { exact: true })
    .locator("../..");
  await fieldGroup.getByText("上書き", { exact: true }).click();
  await fieldGroup.getByText("有効", { exact: true }).click();
  await expect(panel).toContainText("抽出する項目定義が未設定のため、項目抽出は実行されません");
  await expectNoPageOverflow(page);
});

test("処理途中の文書は設定を編集できない", async ({ page }) => {
  await mockWorkspace(page, { documentStatus: "REVIEW" });
  await page.goto("/documents/doc-1");

  const panel = page.getByRole("region", { name: "処理レシピ" });
  await panel.getByRole("button", { name: "処理設定を編集" }).click();

  await expect(panel).toContainText("処理途中または文書処理の実行中は設定を変更できません");
  await expect(
    panel.getByRole("group", { name: "文書解析" }).getByText("上書き")
  ).toBeDisabled();
  await expectNoPageOverflow(page);
});

test("確認待ちレシピの分割を一時設定でプレビューする", async ({ page }) => {
  const state = await mockWorkspace(page, { documentStatus: "REVIEW" });
  await page.goto("/documents/doc-1");

  await page.getByRole("tab", { name: "Chunk" }).click();
  const preview = page.getByRole("region", { name: "分割プレビュー" });
  await expect(preview).toContainText(
    "保存済みの抽出結果を一時設定で分割します。レシピ設定や工程状態は変更しません。"
  );
  await expect(page.getByText("chunk はまだ作成されていません。")).toBeVisible();
  await preview.getByRole("combobox", { name: "分割方式" }).click();
  await page.getByRole("option", { name: "構造認識" }).click();
  await preview.getByLabel("chunk サイズ(文字)", { exact: true }).fill("640");
  await preview.getByRole("button", { name: "プレビュー実行" }).click();

  await expect(preview).toContainText("件数");
  await expect(preview).toContainText("24 文字");
  await expect(page.getByText("経費申請は部門長の承認後、経理部が確認します。"))
    .toBeVisible();
  expect(state.previewPosts()).toBe(1);
  expect(state.previewPayload()).toMatchObject({
    chunk_size: 640,
    chunk_context_header_enabled: true,
  });
  await expectNoPageOverflow(page);
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 760, collapseSidebar: false },
  { name: "mobile", width: 375, height: 812, collapseSidebar: true },
]) {
  test(`分割プレビューの意味境界方式は推奨値を適用する (${viewport.name})`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapseSidebar) {
      await page.addInitScript(() => {
        window.localStorage.setItem(
          "production-ready-rag.ui",
          JSON.stringify({ state: { sidebarCollapsed: true }, version: 0 })
        );
      });
    }
    const state = await mockWorkspace(page, { documentStatus: "REVIEW" });
    await page.goto("/documents/doc-1");
    await page.getByRole("tab", { name: "Chunk" }).click();

    const preview = page.getByRole("region", { name: "分割プレビュー" });
    const strategy = preview.getByRole("combobox", { name: "分割方式" });
    await strategy.click();
    await page.getByRole("option", { name: "見出し単位" }).click();

    let details = preview.locator("details").filter({
      hasText: "長大な単位の再分割(詳細設定)",
    });
    await expect(details).not.toHaveAttribute("open", "");
    await details.getByText("長大な単位の再分割(詳細設定)").click();
    await expect(details.getByLabel("見出し内の再分割上限(文字)")).toHaveValue("32000");
    await expect(details.getByLabel("再分割時の重複文字数")).toHaveValue("0");

    await strategy.click();
    await page.getByRole("option", { name: "ページ単位" }).click();
    details = preview.locator("details").filter({
      hasText: "長大な単位の再分割(詳細設定)",
    });
    await expect(details).not.toHaveAttribute("open", "");
    await details.getByText("長大な単位の再分割(詳細設定)").click();
    await expect(details.getByLabel("ページ内の再分割上限(文字)")).toHaveValue("32000");
    await expect(details.getByLabel("再分割時の重複文字数")).toHaveValue("0");

    await preview.getByRole("button", { name: "プレビュー実行" }).click();
    expect(state.previewPayload()).toMatchObject({
      chunking_strategy: "page_level",
      chunk_size: 32_000,
      chunk_overlap: 0,
    });
    await expectNoPageOverflow(page);
  });
}

test("分割プレビュー失敗を画面内に表示する", async ({ page }) => {
  await mockWorkspace(page, { documentStatus: "REVIEW", previewFails: true });
  await page.goto("/documents/doc-1");

  await page.getByRole("tab", { name: "Chunk" }).click();
  const preview = page.getByRole("region", { name: "分割プレビュー" });
  await preview.getByRole("button", { name: "プレビュー実行" }).click();

  await expect(preview).toContainText("分割サービスに接続できませんでした。");
  await expectNoPageOverflow(page);
});

test("未保存の抽出修正がある間は分割プレビューを無効にする", async ({ page }) => {
  const state = await mockWorkspace(page, { documentStatus: "REVIEW" });
  await page.goto("/documents/doc-1");

  await page.getByRole("tab", { name: "構造化要素" }).click();
  await page.getByRole("button", { name: "構造化要素を修正" }).click();
  await page.locator('textarea[id^="review-edit-"]').first().fill("修正中の経費申請規程");
  await page.getByRole("tab", { name: "Chunk" }).click();

  const preview = page.getByRole("region", { name: "分割プレビュー" });
  await expect(preview).toContainText(
    "未保存の抽出修正があります。修正を保存してからプレビューしてください。"
  );
  await expect(preview.getByRole("button", { name: "プレビュー実行" })).toBeDisabled();
  expect(state.previewPosts()).toBe(0);
  await expectNoPageOverflow(page);
});
