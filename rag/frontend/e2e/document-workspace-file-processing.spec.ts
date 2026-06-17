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
  await page.route("**/api/knowledge-bases**", async (route) => {
    await route.fulfill({
      json: {
        data: {
          items: [{ id: "kb-1", name: "社内規程", document_count: 1 }],
          total: 1,
          limit: 100,
          offset: 0,
          has_next: false,
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
});

test("文書 workspace で chunk と構造化 block を相互に確認できる", async ({ page }) => {
  await mockDocumentWorkspace(page);

  await page.goto("/documents/doc-1");

  await expect(page.getByRole("heading", { name: "原本プレビュー" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "抽出本文" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Chunk / Citation" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "取込セグメント" })).toBeVisible();

  const chunkPanel = page
    .getByRole("heading", { name: "Chunk / Citation" })
    .locator("xpath=ancestor::section[1]");
  const chunkButton = chunkPanel.getByRole("button", { name: /交通費は1000円/ });
  await chunkButton.focus();
  await page.keyboard.press("Enter");
  await expect(chunkButton).toHaveAttribute("aria-pressed", "true");
  await expect(
    page.getByText(/位置: p\.1 \/ bbox x=0\.0% y=0\.0% w=100\.0% h=40\.0%/)
  ).toBeVisible();
  await expect(page.getByTestId("bbox-page-map")).toBeVisible();
  const overlay = page.getByTestId("bbox-overlay");
  const previewOverlay = page.getByTestId("bbox-preview-overlay");
  await expect(overlay).toBeVisible();
  await expect(page.getByTestId("bbox-preview-page")).toBeVisible();
  await expect(previewOverlay).toBeVisible();
  await expect(page.getByTestId("bbox-content-overlay")).toHaveCount(0);
  await expect(overlay).toHaveAttribute("data-bbox-mode", "xyxy");
  await expect(overlay).toHaveAttribute("data-bbox-unit", "percent");
  await expect(overlay).toHaveAttribute("style", /left: 0%; top: 0%; width: 100%; height: 40%;/);
  await expect(previewOverlay).toHaveAttribute("data-bbox-mode", "xyxy");
  await expect(previewOverlay).toHaveAttribute("data-bbox-unit", "percent");
  await expect(previewOverlay).toHaveAttribute(
    "style",
    /left: 0%; top: 0%; width: 100%; height: 40%;/
  );
  await expect(page.getByRole("button", { name: /tbl-1 \/ local_text_structure/ })).toHaveAttribute(
    "aria-pressed",
    "true"
  );

  const extractionPanel = page
    .getByRole("heading", { name: "抽出本文" })
    .locator("xpath=ancestor::section[1]");
  const titleButton = extractionPanel.getByRole("button", {
    name: /経費申請[\s\S]*el-0000/,
  });
  const titleChunkButton = chunkPanel.getByRole("button", { name: /経費申請の概要/ });
  await titleButton.click();
  await expect(titleButton).toHaveAttribute("aria-pressed", "true");
  await expect(titleChunkButton).toHaveAttribute("aria-pressed", "true");
  await expect(chunkButton).toHaveAttribute("aria-pressed", "false");
  await expect(
    page.getByText(/位置: p\.1 \/ bbox x=25\.0% y=25\.0% w=25\.0% h=25\.0%/)
  ).toBeVisible();
  await expect(overlay).toHaveAttribute("data-bbox-mode", "xyxy");
  await expect(overlay).toHaveAttribute("data-bbox-unit", "absolute");
  await expect(overlay).toHaveAttribute("style", /left: 25%; top: 25%; width: 25%; height: 25%;/);
  await expect(previewOverlay).toHaveAttribute("data-bbox-mode", "xyxy");
  await expect(previewOverlay).toHaveAttribute("data-bbox-unit", "absolute");
  await expect(previewOverlay).toHaveAttribute(
    "style",
    /left: 25%; top: 25%; width: 25%; height: 25%;/
  );
  await expectNoHorizontalOverflow(page);
});

test("chunk 取得失敗時は workspace 内にエラー状態を表示する", async ({ page }) => {
  await mockDocumentWorkspace(page, { chunksError: true });

  await page.goto("/documents/doc-1");

  await expect(page.getByText("chunk を取得できません")).toBeVisible();
  await expect(page.getByText("索引状態を確認して再読み込みしてください。")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("画像 preview は同一 surface 上で bbox overlay を位置決めする", async ({ page }) => {
  await mockDocumentWorkspace(page, { imagePreview: true });

  await page.goto("/documents/doc-1");

  const chunkPanel = page
    .getByRole("heading", { name: "Chunk / Citation" })
    .locator("xpath=ancestor::section[1]");
  const tableChunkButton = chunkPanel.getByRole("button", { name: /交通費は1000円/ });
  await tableChunkButton.click();

  const surface = page.getByTestId("preview-image-surface");
  const overlay = page.getByTestId("bbox-content-overlay");
  await expect(surface).toBeVisible();
  await expect(overlay).toBeVisible();
  await expect(page.getByTestId("bbox-preview-page")).toHaveCount(0);
  await expect(overlay).toHaveAttribute("data-bbox-mode", "xyxy");
  await expect(overlay).toHaveAttribute("data-bbox-unit", "percent");
  await expect(overlay).toHaveAttribute(
    "style",
    /left: 0%; top: 0%; width: 100%; height: 40%;/
  );

  const surfaceBox = await surface.boundingBox();
  const overlayBox = await overlay.boundingBox();
  expect(surfaceBox).not.toBeNull();
  expect(overlayBox).not.toBeNull();
  expect(overlayBox!.width).toBeCloseTo(surfaceBox!.width, 1);
  expect(overlayBox!.height).toBeCloseTo(surfaceBox!.height * 0.4, 1);
  await expectNoHorizontalOverflow(page);
});

test("明示された xywh bbox mode で citation overlay を位置決めする", async ({ page }) => {
  await mockDocumentWorkspace(page, { chunkBboxMode: "xywh" });

  await page.goto("/documents/doc-1");

  const chunkPanel = page
    .getByRole("heading", { name: "Chunk / Citation" })
    .locator("xpath=ancestor::section[1]");
  await chunkPanel.getByRole("button", { name: /交通費は1000円/ }).click();

  await expect(
    page.getByText(/位置: p\.1 \/ bbox x=25\.0% y=10\.0% w=50\.0% h=40\.0%/)
  ).toBeVisible();
  const overlay = page.getByTestId("bbox-overlay");
  await expect(overlay).toHaveAttribute("data-bbox-mode", "xywh");
  await expect(overlay).toHaveAttribute("data-bbox-unit", "percent");
  await expect(overlay).toHaveAttribute(
    "style",
    /left: 25%; top: 10%; width: 50%; height: 40%;/
  );
  await expectNoHorizontalOverflow(page);
});

test("element_id 深リンクは構造化 block をフォーカスして preview bbox に定位する", async ({ page }) => {
  await mockDocumentWorkspace(page);

  await page.goto("/documents/doc-1?element_id=tbl-1");

  const extractionPanel = page
    .getByRole("heading", { name: "抽出本文" })
    .locator("xpath=ancestor::section[1]");
  const tableElementButton = extractionPanel.getByRole("button", {
    name: /交通費は1000円[\s\S]*tbl-1/,
  });
  await expect(tableElementButton).toHaveAttribute("aria-pressed", "true");
  await expect(tableElementButton).toBeFocused();
  await expect(
    page.getByText(/位置: p\.1 \/ bbox x=0\.0% y=0\.0% w=100\.0% h=40\.0%/)
  ).toBeVisible();
  await expect(page.getByTestId("bbox-preview-overlay")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("取込セグメント失敗時は原因と復旧導線を表示する", async ({ page }) => {
  const state = await mockDocumentWorkspace(page, { segmentError: true });

  await page.goto("/documents/doc-1");

  await expect(page.getByRole("heading", { name: "取込セグメント" })).toBeVisible();
  await expect(page.getByText("enterprise_ai_response_validation_error")).toBeVisible();
  await expect(page.getByText("原因")).toBeVisible();
  await expect(page.getByText(/confidence/)).toBeVisible();
  await expect(
    page.getByText("一時的な応答不整合の可能性があります。再試行すると失敗 segment のみ再処理します。")
  ).toBeVisible();
  await page.getByRole("button", { name: "失敗 segment を再試行" }).click();
  await expect.poll(() => state.retryRequest).toEqual({
    method: "POST",
    path: "/api/documents/doc-1/ingestion-jobs",
    force: null,
  });
  await expectNoHorizontalOverflow(page);
});

async function mockDocumentWorkspace(
  page: Page,
  options: {
    chunkBboxMode?: "xywh";
    chunksError?: boolean;
    imagePreview?: boolean;
    segmentError?: boolean;
  } = {}
) {
  const state: {
    retryRequest: { method: string; path: string; force: string | null } | null;
  } = {
    retryRequest: null,
  };
  await page.route("**/api/documents/doc-1", async (route) => {
    await route.fulfill({
      json: {
        data: documentDetail({
          imagePreview: options.imagePreview,
          status: options.segmentError ? "ERROR" : "INDEXED",
        }),
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await page.route("**/api/documents/doc-1/ingestion-jobs**", async (route) => {
    const url = new URL(route.request().url());
    state.retryRequest = {
      method: route.request().method(),
      path: url.pathname,
      force: url.searchParams.get("force"),
    };
    await route.fulfill({
      json: {
        data: retrySegmentsJob(),
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await page.route("**/api/documents/ingestion-jobs/job-retry-segments", async (route) => {
    await route.fulfill({
      json: {
        data: retrySegmentsJob(),
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await page.route("**/api/documents/doc-1/knowledge-bases", async (route) => {
    await route.fulfill({
      json: {
        data: [{ id: "kb-1", name: "社内規程" }],
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await page.route("**/api/documents/doc-1/content", async (route) => {
    if (options.imagePreview) {
      await route.fulfill({
        status: 200,
        headers: { "content-type": "image/svg+xml" },
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="612" height="792"><rect width="612" height="792" fill="white"/><text x="24" y="80">TOTAL 1000 JPY</text></svg>',
      });
      return;
    }
    await route.fulfill({
      status: 200,
      headers: { "content-type": "text/plain; charset=utf-8" },
      body: "経費申請\n交通費は1000円です。",
    });
  });
  await page.route("**/api/documents/doc-1/chunks", async (route) => {
    if (options.chunksError) {
      await route.fulfill({
        status: 500,
        json: {
          data: null,
          error_messages: ["chunk error"],
          warning_messages: [],
        },
      });
      return;
    }
    await route.fulfill({
      json: {
        data: [
          {
            document_id: "doc-1",
            chunk_id: "doc-1:0",
            chunk_index: 0,
            text: "経費申請の概要です。",
            page_start: 1,
            page_end: 1,
            bbox: null,
            section_path: "経費申請",
            content_kind: "text",
            chunk_group_id: "grp-1",
            source_parser: "local_text_structure",
            element_ids: ["el-0000"],
            metadata: { chunk_profile: "structure_v1" },
          },
          {
            document_id: "doc-1",
            chunk_id: "doc-1:1",
            chunk_index: 1,
            text: "交通費は1000円です。",
            page_start: 1,
            page_end: 1,
            bbox: options.chunkBboxMode === "xywh" ? [25, 10, 50, 40] : [0, 0, 100, 40],
            section_path: "経費申請 > 料金表",
            content_kind: "table",
            chunk_group_id: "grp-2",
            source_parser: "local_text_structure",
            element_ids: ["tbl-1"],
            metadata:
              options.chunkBboxMode === "xywh"
                ? { bbox_coordinate_mode: "xywh", chunk_profile: "structure_v1" }
                : { chunk_profile: "structure_v1" },
          },
        ],
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await page.route("**/api/documents/doc-1/ingestion-segments", async (route) => {
    const failedSegment = {
      segment_id: "doc-1:p1-3",
      document_id: "doc-1",
      status: "FAILED",
      parser_backend: "enterprise_ai",
      parser_profile: "enterprise_ai_pdf_layout",
      page_start: 1,
      page_end: 3,
      attempt_count: 2,
      artifact_path: null,
      error_code: "enterprise_ai_response_validation_error",
      error_message:
        "OCI Enterprise AI VLM response が StructuredExtraction schema と一致しません。失敗項目: confidence: less_than_equal。",
    };
    await route.fulfill({
      json: {
        data: options.segmentError
          ? [failedSegment]
          : [
              {
                segment_id: "doc-1:source",
                document_id: "doc-1",
                status: "SUCCEEDED",
                parser_backend: "local_partition",
                parser_profile: "local_text_structure",
                page_start: 1,
                page_end: 1,
                attempt_count: 1,
                artifact_path: "local://doc-1",
                error_code: null,
                error_message: null,
              },
            ],
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  return state;
}

function retrySegmentsJob() {
  return {
    id: "job-retry-segments",
    document_id: "doc-1",
    status: "QUEUED",
    parser_profile: "enterprise_ai_pdf_layout",
    quality_warnings: [],
    skip_reason: null,
    error_message: null,
    attempt_count: 0,
    max_attempts: 3,
    queued_at: "2026-06-15T00:00:05Z",
    started_at: null,
    finished_at: null,
  };
}

function documentDetail(options: { imagePreview?: boolean; status?: string } = {}) {
  return {
    id: "doc-1",
    file_name: options.imagePreview ? "receipt.png" : "policy.txt",
    status: options.status ?? "INDEXED",
    category_name: null,
    content_type: options.imagePreview ? "image/png" : "text/plain",
    file_size_bytes: 64,
    content_sha256: "a".repeat(64),
    duplicate_of_document_id: null,
    uploaded_at: "2026-06-15T00:00:00Z",
    indexed_at: "2026-06-15T00:00:03Z",
    object_storage_path: options.imagePreview ? "local://receipt.png" : "local://policy.txt",
    extraction: {
      raw_text: "経費申請\n交通費は1000円です。",
      document_type: "規程",
      confidence: 0.98,
      warnings: [],
      pages: [{ page_number: 1, width: 612, height: 792, element_ids: ["el-0000", "tbl-1"] }],
      elements: [
        {
          kind: "title",
          text: "経費申請",
          order: 0,
          element_id: "el-0000",
          content_kind: "text",
          source_parser: "local_text_structure",
          page_number: 1,
          bbox: [153, 198, 306, 396],
          section_path: ["経費申請"],
          confidence: 0.98,
          metadata: {},
        },
        {
          kind: "table",
          text: "交通費は1000円です。",
          order: 1,
          element_id: "tbl-1",
          content_kind: "table",
          source_parser: "local_text_structure",
          page_number: 1,
          bbox: [0, 0, 100, 40],
          section_path: ["経費申請", "料金表"],
          confidence: 0.88,
          metadata: {},
        },
      ],
      tables: [],
      assets: [],
      parser_artifacts: { parser_backend: "local_partition" },
    },
    error_message: null,
    knowledge_bases: [{ id: "kb-1", name: "社内規程" }],
    source_profile: {
      original_file_name: options.imagePreview ? "receipt.png" : "policy.txt",
      sanitized_file_name: options.imagePreview ? "receipt.png" : "policy.txt",
      extension: options.imagePreview ? ".png" : ".txt",
      content_type: options.imagePreview ? "image/png" : "text/plain",
      inferred_content_type: options.imagePreview ? "image/png" : "text/plain",
      file_size_bytes: 64,
      content_sha256: "a".repeat(64),
      modality: options.imagePreview ? "image" : "text",
      parser_profile: options.imagePreview ? "enterprise_ai_image_ocr" : "local_text_structure",
      parser_backend: options.imagePreview ? "enterprise_ai" : "local_partition",
      parser_version: "v1",
      preview_kind: options.imagePreview ? "image" : "text",
      text_charset: options.imagePreview ? null : "utf-8",
      duplicate_of_document_id: null,
      unsupported_reason: null,
      quality_status: "ready",
      quality_warnings: [],
    },
  };
}

async function expectNoHorizontalOverflow(page: Page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth
    )
  ).toBe(true);
}
