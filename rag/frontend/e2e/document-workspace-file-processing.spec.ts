import { expect, type Locator, type Page, test } from "@playwright/test";
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
  await expect(page.getByRole("heading", { name: "抽出エクスポート" })).toBeVisible();
  await expect(page.getByText("<!-- page: 1 -->")).toBeVisible();
  await page.getByRole("button", { name: "HTML" }).click();
  await expect(page.getByText("<article")).toBeVisible();
  await expect(page.getByText("<h1>経費申請</h1>")).toBeVisible();
  await expect(page.getByText('<table data-element-id="tbl-1"')).toBeVisible();
  await page.getByRole("button", { name: "JSON" }).click();
  await expect(page.getByText('"document_type": "規程"')).toBeVisible();
  await page.getByRole("button", { name: "Chunks" }).click();
  await expect(page.getByText('"chunk_id": "doc-1:0"')).toBeVisible();

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

test("取込解析エンジンは segment parser だけを表示する", async ({
  page,
}) => {
  await mockDocumentWorkspace(page, { pdfPreview: true, mineruSegment: true });

  await page.goto("/documents/doc-1");

  const sourcePanel = page
    .getByRole("heading", { name: "原本と取込の処理情報" })
    .locator("xpath=ancestor::section[1]");
  await expect(sourcePanel.getByText("取込解析エンジン")).toBeVisible();
  await expect(sourcePanel.getByText("MinerU")).toBeVisible();
  await expect(sourcePanel.getByText("mineru_adapter")).toHaveCount(0);
  await expect(
    sourcePanel.getByText("アップロード時の初期判定: OCI Enterprise AI / v1")
  ).toHaveCount(0);
  await expect(
    sourcePanel.getByText("原本メタデータに追加の確認事項はありません。")
  ).toHaveCount(0);
});

test("狭い画面幅(375px)でも文書 workspace がページを横スクロール(崩れ)させない", async ({ page }) => {
  await mockDocumentWorkspace(page);

  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/documents/doc-1");

  await expect(page.getByRole("heading", { name: "原本プレビュー" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Chunk / Citation" })).toBeVisible();
  await expectNoPageOverflow(page);
});

test("PDF 原本プレビューは左サイドバーを初期表示しない", async ({ page }) => {
  await mockDocumentWorkspace(page, { pdfPreview: true });

  await page.goto("/documents/doc-1");

  const pdfFrame = page.locator('iframe[title="policy.pdf"]');
  await expect(pdfFrame).toHaveAttribute(
    "src",
    /\/api\/documents\/doc-1\/content#page=1&pagemode=none&navpanes=0$/
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

test("metadata の bbox unit を優先して citation overlay を位置決めする", async ({ page }) => {
  await mockDocumentWorkspace(page, { chunkBboxUnit: "absolute" });

  await page.goto("/documents/doc-1");

  const chunkPanel = page
    .getByRole("heading", { name: "Chunk / Citation" })
    .locator("xpath=ancestor::section[1]");
  await chunkPanel.getByRole("button", { name: /交通費は1000円/ }).click();

  await expect(
    page.getByText(/位置: p\.1 \/ bbox x=4\.1% y=1\.3% w=4\.1% h=3\.8%/)
  ).toBeVisible();
  const overlay = page.getByTestId("bbox-overlay");
  await expect(overlay).toHaveAttribute("data-bbox-mode", "xyxy");
  await expect(overlay).toHaveAttribute("data-bbox-unit", "absolute");
  await expect(overlay).toHaveAttribute(
    "style",
    /left: 4\.08497.*%; top: 1\.26263.*%; width: 4\.08497.*%; height: 3\.78788.*%;/
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

test("formula cell 深リンクは表セルをフォーカスして cell bbox に定位する", async ({ page }) => {
  await mockDocumentWorkspace(page);

  await page.goto("/documents/doc-1?chunk_id=doc-1:1&table_id=tbl-1&formula_cell_ref=B2&page=1");

  const extractionPanel = page
    .getByRole("heading", { name: "抽出本文" })
    .locator("xpath=ancestor::section[1]");
  const targetCell = extractionPanel
    .getByTestId("extraction-table-cell")
    .filter({ hasText: "B2" });
  await expect(targetCell).toHaveAttribute("aria-pressed", "true");
  await expect(targetCell).toBeFocused();
  await expect(targetCell).toContainText("1000円");
  await expect(
    page.getByText(/位置: p\.1 \/ bbox x=50\.0% y=10\.0% w=25\.0% h=20\.0%/)
  ).toBeVisible();
  const overlay = page.getByTestId("bbox-overlay");
  await expect(overlay).toBeVisible();
  await expect(overlay).toHaveAttribute("data-bbox-mode", "xyxy");
  await expect(overlay).toHaveAttribute("data-bbox-unit", "percent");
  await expect(overlay).toHaveAttribute(
    "style",
    /left: 50%; top: 10%; width: 25%; height: 20%;/
  );
  await expectNoHorizontalOverflow(page);
});

test("citation 深リンクの bbox fallback で chunk 欠損時も preview に定位する", async ({ page }) => {
  await mockDocumentWorkspace(page, { chunksEmpty: true });

  await page.goto(
    "/documents/doc-1?chunk_id=missing:chunk&page=1&bbox=10,15,40,30&bbox_mode=xywh&bbox_unit=percent&page_width=612&page_height=792"
  );

  await expect(
    page.getByText(/位置: p\.1 \/ bbox x=10\.0% y=15\.0% w=40\.0% h=30\.0%/)
  ).toBeVisible();
  await expect(page.getByText("chunk はまだ作成されていません。")).toBeVisible();
  const overlay = page.getByTestId("bbox-overlay");
  await expect(overlay).toBeVisible();
  await expect(overlay).toHaveAttribute("data-bbox-mode", "xywh");
  await expect(overlay).toHaveAttribute("data-bbox-unit", "percent");
  await expect(overlay).toHaveAttribute(
    "style",
    /left: 10%; top: 15%; width: 40%; height: 30%;/
  );
  await expectNoHorizontalOverflow(page);
});

test("citation 深リンクは page rotation を反映して bbox overlay を定位する", async ({ page }) => {
  await mockDocumentWorkspace(page, { chunksEmpty: true });

  await page.goto(
    "/documents/doc-1?chunk_id=missing:chunk&page=1&bbox=10,15,40,30&bbox_mode=xywh&bbox_unit=percent&page_width=612&page_height=792&page_rotation=90"
  );

  await expect(
    page.getByText(/位置: p\.1 \/ bbox x=15\.0% y=50\.0% w=30\.0% h=40\.0%/)
  ).toBeVisible();
  const overlay = page.getByTestId("bbox-overlay");
  await expect(overlay).toBeVisible();
  await expect(overlay).toHaveAttribute("data-bbox-mode", "xywh");
  await expect(overlay).toHaveAttribute("data-bbox-unit", "percent");
  await expect(overlay).toHaveAttribute(
    "style",
    /left: 15%; top: 50%; width: 30%; height: 40%;/
  );
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
    path: "/api/documents/doc-1/ingestion-segments/retry",
  });
  await expectNoHorizontalOverflow(page);
});

test("文書 workspace はこの文書の取込 job と時間線を表示する", async ({ page }) => {
  await mockDocumentWorkspace(page, {
    latestJobStatus: "RUNNING",
    latestJobStartedAt: new Date(Date.now() - 2_000).toISOString(),
    pdfPreview: true,
  });

  await page.goto("/documents/doc-1");

  const panel = page
    .getByRole("heading", { name: "この文書の取込ジョブ" })
    .locator("xpath=ancestor::section[1]");
  await expect(panel.getByText("取込中")).toBeVisible();
  await expect(panel.getByText("抽出", { exact: true })).toBeVisible();
  await expect(panel.getByText(/job: job-runn/)).toBeVisible();
  await expect(panel.getByText("投入")).toBeVisible();
  await expect(panel.getByText("開始")).toBeVisible();
  await expect(panel.getByText("1/3 回")).toBeVisible();
  const elapsed = panel.getByTestId("ingestion-job-elapsed");
  const firstElapsed = await elapsed.textContent();
  await expect.poll(() => elapsed.textContent(), { timeout: 4_000 }).not.toBe(firstElapsed);
  await expect(
    panel.getByText("この job が完了するまで文書状態・segment・抽出結果を自動更新します。")
  ).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("取込ジョブ投入後に workspace の本文 export と chunk を自動更新する", async ({ page }) => {
  await mockDocumentWorkspace(page, { autoRefreshAfterEnqueue: true });

  await page.goto("/documents/doc-1");

  await expect(page.getByText("chunk はまだ作成されていません。")).toBeVisible();
  await expect(page.getByText("表示できる抽出エクスポートはありません。")).toBeVisible();

  await page.getByRole("button", { name: "取込ジョブに投入" }).click();

  await expect(page.getByText("取込ジョブをキューに投入しました。")).toBeVisible();
  await expect(page.getByRole("button", { name: /経費申請の概要です。/ })).toBeVisible({
    timeout: 9_000,
  });
  await expect(page.getByText("<!-- page: 1 -->")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("取込セグメントが多い場合は高さ固定で内部スクロールする", async ({ page }) => {
  await mockDocumentWorkspace(page, { segmentCount: 30 });

  await page.goto("/documents/doc-1");

  const panel = page
    .getByRole("heading", { name: "取込セグメント" })
    .locator("xpath=ancestor::section[1]");
  const list = panel.locator("ol");

  await expect(page.getByText("30 件")).toBeVisible();
  await expect(list.getByText("p.1-10")).toBeVisible();
  await expect(await isScrollable(list)).toBe(true);
  await expectNoHorizontalOverflow(page);
});

async function mockDocumentWorkspace(
  page: Page,
  options: {
    chunkBboxMode?: "xywh";
    chunkBboxUnit?: "absolute";
    chunksEmpty?: boolean;
    chunksError?: boolean;
    imagePreview?: boolean;
    pdfPreview?: boolean;
    segmentError?: boolean;
    segmentCount?: number;
    mineruSegment?: boolean;
    latestJobStatus?: "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED";
    latestJobStartedAt?: string;
    autoRefreshAfterEnqueue?: boolean;
  } = {}
) {
  const state: {
    retryRequest: { method: string; path: string } | null;
    enqueued: boolean;
  } = {
    retryRequest: null,
    enqueued: false,
  };
  await page.route("**/api/documents/doc-1", async (route) => {
    await route.fulfill({
      json: {
        data: documentDetail({
          imagePreview: options.imagePreview,
          pdfPreview: options.pdfPreview,
          status:
            options.segmentError || (options.autoRefreshAfterEnqueue && !state.enqueued)
              ? "ERROR"
              : "INDEXED",
        }),
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await page.route("**/api/documents/doc-1/ingestion-segments/retry", async (route) => {
    const url = new URL(route.request().url());
    state.retryRequest = {
      method: route.request().method(),
      path: url.pathname,
    };
    await route.fulfill({
      json: {
        data: retrySegmentsJob(),
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await page.route("**/api/documents/doc-1/ingestion-jobs**", async (route) => {
    if (route.request().method() === "POST") {
      state.enqueued = true;
      await route.fulfill({
        json: {
          data: ingestionJob("QUEUED"),
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }
    await route.fulfill({
      json: {
        data: [
          ingestionJob(options.latestJobStatus ?? "SUCCEEDED", {
            startedAt: options.latestJobStartedAt,
          }),
        ],
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
    if (options.pdfPreview) {
      await route.fulfill({
        status: 200,
        headers: { "content-type": "application/pdf" },
        body: [
          "%PDF-1.1",
          "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
          "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj",
          "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >> endobj",
          "trailer << /Root 1 0 R >>",
          "%%EOF",
        ].join("\n"),
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
    if (options.chunksEmpty || (options.autoRefreshAfterEnqueue && !state.enqueued)) {
      await route.fulfill({
        json: {
          data: [],
          error_messages: [],
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
            bbox:
              options.chunkBboxMode === "xywh" || options.chunkBboxUnit
                ? [25, 10, 50, 40]
                : [0, 0, 100, 40],
            section_path: "経費申請 > 料金表",
            content_kind: "table",
            chunk_group_id: "grp-2",
            source_parser: "local_text_structure",
            element_ids: ["tbl-1"],
            metadata:
              options.chunkBboxMode === "xywh" || options.chunkBboxUnit
                ? {
                    ...(options.chunkBboxMode === "xywh"
                      ? { bbox_coordinate_mode: "xywh" }
                      : {}),
                    ...(options.chunkBboxUnit ? { bbox_unit: options.chunkBboxUnit } : {}),
                    chunk_profile: "structure_v1",
                  }
                : { chunk_profile: "structure_v1" },
          },
        ],
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await page.route("**/api/documents/doc-1/extraction-export**", async (route) => {
    const url = new URL(route.request().url());
    const format = url.searchParams.get("format") ?? "markdown";
    const exportData =
      options.autoRefreshAfterEnqueue && !state.enqueued
        ? {
            ...extractionExport(format),
            content: "",
            payload: {},
            chunks: [],
            page_count: 0,
            element_count: 0,
            table_count: 0,
            asset_count: 0,
          }
        : extractionExport(format);
    await route.fulfill({
      json: {
        data: exportData,
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await page.route("**/api/documents/doc-1/ingestion-segments", async (route) => {
    const failedSegment = {
      segment_id: "doc-1:p1-10",
      document_id: "doc-1",
      status: "FAILED",
      parser_backend: "enterprise_ai",
      parser_profile: "enterprise_ai_pdf_layout",
      page_start: 1,
      page_end: 10,
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
          : ingestionSegments(options.segmentCount ?? 1, {
              mineru: options.mineruSegment,
            }),
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  return state;
}

function ingestionSegments(count: number, options: { mineru?: boolean } = {}) {
  return Array.from({ length: count }, (_, index) => {
    const start = index * 10 + 1;
    const end = start + 9;
    const status = index < 4 ? "SUCCEEDED" : index === 4 ? "RUNNING" : "QUEUED";
    const mineru = Boolean(options.mineru);
    return {
      segment_id: `doc-1:p${start}-${end}`,
      document_id: "doc-1",
      status,
      parser_backend: mineru ? "mineru" : index === 0 ? "local_partition" : "enterprise_ai",
      parser_profile: mineru
        ? "mineru_adapter"
        : index === 0
          ? "local_text_structure"
          : "enterprise_ai_pdf_layout",
      page_start: start,
      page_end: end,
      attempt_count: status === "QUEUED" ? 0 : 1,
      artifact_path: status === "SUCCEEDED" ? `local://doc-1/${start}` : null,
      error_code: null,
      error_message: null,
    };
  });
}

function extractionExport(format: string) {
  const chunks = [
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
  ];
  const payload = {
    raw_text: "経費申請\n交通費は1000円です。",
    document_type: "規程",
    elements: documentDetail().extraction.elements,
  };
  const htmlContent = [
    "<article>",
    "  <h1>経費申請</h1>",
    "  <p>交通費は1000円です。</p>",
    '  <table data-element-id="tbl-1" class="table-block" data-table-id="tbl-1">',
    "    <tbody>",
    '      <tr data-row="0"><th data-table-id="tbl-1" data-row="0" data-col="0">項目</th><th data-table-id="tbl-1" data-row="0" data-col="1">値</th></tr>',
    '      <tr data-row="1"><td data-table-id="tbl-1" data-row="1" data-col="0">交通費</td><td data-table-id="tbl-1" data-row="1" data-col="1">1000円</td></tr>',
    "    </tbody>",
    "  </table>",
    "</article>",
  ].join("\n");
  const content =
    format === "markdown"
      ? "<!-- page: 1 -->\n# 経費申請\n\n交通費は1000円です。"
      : format === "html"
        ? htmlContent
        : JSON.stringify(format === "chunks" ? { chunks } : payload, null, 2);
  return {
    document_id: "doc-1",
    file_name: "policy.txt",
    format,
    content_type:
      format === "markdown"
        ? "text/markdown; charset=utf-8"
        : format === "html"
          ? "text/html; charset=utf-8"
          : "application/json",
    content,
    payload:
      format === "markdown" || format === "html"
        ? {}
        : format === "chunks"
          ? { chunks }
          : payload,
    chunks: format === "chunks" ? chunks : [],
    parser_backend: "local_partition",
    parser_profile: "local_text_structure",
    page_count: 1,
    element_count: 2,
    table_count: 0,
    asset_count: 0,
  };
}

function retrySegmentsJob() {
  return ingestionJob("QUEUED");
}

function ingestionJob(
  status: "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED",
  options: { queuedAt?: string; startedAt?: string; finishedAt?: string } = {}
) {
  return {
    id: status === "RUNNING" ? "job-running-0001" : "job-retry-segments",
    document_id: "doc-1",
    status,
    phase: "EXTRACT",
    parser_profile: "enterprise_ai_pdf_layout",
    quality_warnings: [],
    skip_reason: null,
    error_message: status === "FAILED" ? "取込処理に失敗しました。" : null,
    attempt_count: status === "QUEUED" ? 0 : 1,
    max_attempts: 3,
    queued_at: options.queuedAt ?? "2026-06-15T00:00:05Z",
    started_at: status === "QUEUED" ? null : options.startedAt ?? "2026-06-15T00:00:10Z",
    finished_at:
      status === "SUCCEEDED" || status === "FAILED"
        ? options.finishedAt ?? "2026-06-15T00:00:20Z"
        : null,
  };
}

function documentDetail(
  options: { imagePreview?: boolean; pdfPreview?: boolean; status?: string } = {}
) {
  const fileName = options.pdfPreview
    ? "policy.pdf"
    : options.imagePreview
      ? "receipt.png"
      : "policy.txt";
  const contentType = options.pdfPreview
    ? "application/pdf"
    : options.imagePreview
      ? "image/png"
      : "text/plain";
  const extension = options.pdfPreview ? ".pdf" : options.imagePreview ? ".png" : ".txt";
  const previewKind = options.pdfPreview ? "pdf" : options.imagePreview ? "image" : "text";
  const modality = options.pdfPreview ? "pdf" : options.imagePreview ? "image" : "text";
  const parserProfile = options.pdfPreview
    ? "enterprise_ai_pdf_layout"
    : options.imagePreview
      ? "enterprise_ai_image_ocr"
      : "local_text_structure";
  const parserBackend =
    options.imagePreview || options.pdfPreview ? "enterprise_ai" : "local_partition";

  return {
    id: "doc-1",
    file_name: fileName,
    status: options.status ?? "INDEXED",
    category_name: null,
    content_type: contentType,
    file_size_bytes: 64,
    content_sha256: "a".repeat(64),
    duplicate_of_document_id: null,
    uploaded_at: "2026-06-15T00:00:00Z",
    indexed_at: "2026-06-15T00:00:03Z",
    object_storage_path: `local://${fileName}`,
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
      tables: [
        {
          table_id: "tbl-1",
          element_id: "tbl-1",
          page_number: 1,
          caption: "料金表",
          metadata: { bbox_unit: "percent" },
          cells: [
            {
              row: 0,
              col: 0,
              text: "項目",
              row_span: 1,
              col_span: 1,
              page_number: 1,
              bbox: [0, 0, 50, 10],
              confidence: 0.95,
              metadata: { cell_ref: "A1", bbox_unit: "percent" },
            },
            {
              row: 0,
              col: 1,
              text: "値",
              row_span: 1,
              col_span: 1,
              page_number: 1,
              bbox: [50, 0, 75, 10],
              confidence: 0.95,
              metadata: { cell_ref: "B1", bbox_unit: "percent" },
            },
            {
              row: 1,
              col: 0,
              text: "交通費",
              row_span: 1,
              col_span: 1,
              page_number: 1,
              bbox: [0, 10, 50, 30],
              confidence: 0.92,
              metadata: { cell_ref: "A2", bbox_unit: "percent" },
            },
            {
              row: 1,
              col: 1,
              text: "1000円",
              row_span: 1,
              col_span: 1,
              page_number: 1,
              bbox: [50, 10, 75, 30],
              confidence: 0.92,
              metadata: {
                cell_ref: "B2",
                formula_cell_ref: "B2",
                formula: "=SUM(B2)",
                bbox_unit: "percent",
              },
            },
          ],
        },
      ],
      assets: [],
      parser_artifacts: { parser_backend: "local_partition" },
    },
    error_message: null,
    knowledge_bases: [{ id: "kb-1", name: "社内規程" }],
    source_profile: {
      original_file_name: fileName,
      sanitized_file_name: fileName,
      extension,
      content_type: contentType,
      inferred_content_type: contentType,
      file_size_bytes: 64,
      content_sha256: "a".repeat(64),
      modality,
      parser_profile: parserProfile,
      parser_backend: parserBackend,
      parser_version: "v1",
      preview_kind: previewKind,
      text_charset: options.imagePreview || options.pdfPreview ? null : "utf-8",
      duplicate_of_document_id: null,
      unsupported_reason: null,
      quality_status: "ready",
      quality_warnings: [],
    },
  };
}

// 既存呼び出しを保ちつつ、documentElement だけでなく main の内部はみ出しも検査する
// 共通ヘルパー(_helpers.ts)へ委譲する。
async function expectNoHorizontalOverflow(page: Page) {
  await expectNoPageOverflow(page);
}

async function isScrollable(locator: Locator): Promise<boolean> {
  return locator.evaluate((el) => el.scrollHeight > el.clientHeight + 1);
}
