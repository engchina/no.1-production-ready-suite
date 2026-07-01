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

  // 左ペインのプレビューは常時表示。右ペインは本文 / 構造化要素 / Chunk / エクスポートのタブ切替。
  await expect(page.getByRole("heading", { name: "原本プレビュー" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "本文テキスト" })).toHaveAttribute(
    "aria-selected",
    "true"
  );
  await expect(page.getByRole("tab", { name: "構造化要素" })).toBeVisible();
  await expect(page.getByRole("tab", { name: /Chunk \/ Citation/ })).toBeVisible();
  await expect(page.getByRole("tab", { name: "抽出エクスポート" })).toBeVisible();
  await expect(page.getByRole("tabpanel").getByRole("button", { name: "本文をコピー" })).toBeVisible();
  // 取込・診断パネルは折りたたみに集約。
  await expect(page.getByText("取込・診断の詳細")).toBeVisible();

  // エクスポートタブ: 形式を切替えると内容が変わる。
  await page.getByRole("tab", { name: "抽出エクスポート" }).click();
  await expect(page.getByText("<!-- page: 1 -->")).toBeVisible();
  await page.getByRole("button", { name: "HTML" }).click();
  await expect(page.getByText("<article")).toBeVisible();
  await expect(page.getByText("<h1>経費申請</h1>")).toBeVisible();
  await expect(page.getByText('<table data-element-id="tbl-1"')).toBeVisible();
  await page.getByRole("button", { name: "JSON" }).click();
  await expect(page.getByText('"document_type": "規程"')).toBeVisible();
  await page.getByRole("button", { name: "Chunks" }).click();
  await expect(page.getByText('"chunk_id": "doc-1:0"')).toBeVisible();

  // Chunk タブ: chunk を選ぶとプレビューに bbox がハイライトされる。
  await page.getByRole("tab", { name: /Chunk \/ Citation/ }).click();
  const chunkButton = page.getByRole("tabpanel").getByRole("button", { name: /交通費は1000円/ });
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

  // 構造化要素タブ: 直前の chunk 選択で紐づく element(tbl-1)が選択済み。
  await page.getByRole("tab", { name: "構造化要素" }).click();
  await expect(page.getByRole("button", { name: /tbl-1 \/ local_text_structure/ })).toHaveAttribute(
    "aria-pressed",
    "true"
  );
  // 構造化要素(title)を選ぶと選択が移り、プレビュー bbox が更新される。
  const titleButton = page
    .getByRole("tabpanel")
    .getByRole("button", { name: /経費申請[\s\S]*el-0000/ });
  await titleButton.click();
  await expect(titleButton).toHaveAttribute("aria-pressed", "true");
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

  // 連動: Chunk タブへ戻ると紐づく chunk が選択され、元の chunk は外れている。
  await page.getByRole("tab", { name: /Chunk \/ Citation/ }).click();
  const chunkPanelAfter = page.getByRole("tabpanel");
  await expect(
    chunkPanelAfter.getByRole("button", { name: /経費申請の概要/ })
  ).toHaveAttribute("aria-pressed", "true");
  await expect(
    chunkPanelAfter.getByRole("button", { name: /交通費は1000円/ })
  ).toHaveAttribute("aria-pressed", "false");
  await expectNoHorizontalOverflow(page);
});

test("成果物の無い recipe は文書レベルの抽出・処理後ファイルへ fallback しない", async ({
  page,
}) => {
  const state = await mockDocumentWorkspace(page, {
    documentStatus: "UPLOADED",
    documentOnlyPreparedArtifact: true,
  });

  await page.goto("/documents/doc-1");

  const previewPanel = page
    .getByRole("heading", { name: "原本プレビュー" })
    .locator("xpath=ancestor::section[1]");
  await expect(previewPanel.getByRole("button", { name: "処理後" })).toBeDisabled();
  await expect(page.getByRole("tabpanel").getByText("交通費は1000円です。")).toHaveCount(0);
  await expect.poll(() => state.extractionExportRequests).toBe(0);
});

test("desktop の空の右ペイン上でも主ページをスクロールできる", async ({ page }) => {
  await mockDocumentWorkspace(page, { documentStatus: "UPLOADED", pdfPreview: true });

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/documents/doc-1");

  const panel = page.getByRole("tabpanel");
  const panelMetrics = await panel.evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      clientHeight: element.clientHeight,
      scrollHeight: element.scrollHeight,
      overscrollBehaviorY: style.overscrollBehaviorY,
    };
  });
  expect(panelMetrics.scrollHeight).toBeLessThanOrEqual(panelMetrics.clientHeight);
  expect(panelMetrics.overscrollBehaviorY).toBe("auto");

  await panel.scrollIntoViewIfNeeded();
  const main = page.locator("main");
  const mainScrollTop = await main.evaluate((element) => element.scrollTop);
  await panel.hover();
  await page.mouse.wheel(0, -800);
  await expect.poll(() => main.evaluate((element) => element.scrollTop)).toBeLessThan(mainScrollTop);
});

test("desktop の右ペインは高さを保ち、境界で主ページへスクロールを引き継ぐ", async ({
  page,
}) => {
  await mockDocumentWorkspace(page, { pdfPreview: true });

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/documents/doc-1");

  const pdfFrame = page.locator('iframe[title="policy.pdf"]');
  const textPanel = page.getByRole("tabpanel");
  const previewHeight = await pdfFrame.evaluate((element) => element.getBoundingClientRect().height);
  const textPanelMetrics = await textPanel.evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      clientHeight: element.clientHeight,
      overflowY: style.overflowY,
      overscrollBehaviorY: style.overscrollBehaviorY,
      scrollbarGutter: style.scrollbarGutter,
    };
  });

  expect(textPanelMetrics.clientHeight).toBeCloseTo(previewHeight, 0);
  expect(textPanelMetrics.overflowY).toBe("auto");
  expect(textPanelMetrics.overscrollBehaviorY).toBe("auto");
  expect(textPanelMetrics.scrollbarGutter).toContain("stable");

  await page.getByRole("tab", { name: "構造化要素" }).click();
  const panel = page.getByRole("tabpanel");
  const panelMetrics = await panel.evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      clientHeight: element.clientHeight,
      scrollHeight: element.scrollHeight,
      overflowY: style.overflowY,
      overscrollBehaviorY: style.overscrollBehaviorY,
    };
  });
  expect(panelMetrics.clientHeight).toBeCloseTo(previewHeight, 0);
  expect(panelMetrics.scrollHeight).toBeGreaterThan(panelMetrics.clientHeight + 1);
  expect(panelMetrics.overflowY).toBe("auto");
  expect(panelMetrics.overscrollBehaviorY).toBe("auto");

  await panel.scrollIntoViewIfNeeded();
  const main = page.locator("main");
  const mainScrollTopBeforePanelScroll = await main.evaluate((element) => element.scrollTop);
  await panel.hover();
  await page.mouse.wheel(0, 400);
  await expect.poll(() => panel.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  await expect.poll(() => main.evaluate((element) => element.scrollTop)).toBe(
    mainScrollTopBeforePanelScroll
  );

  await panel.evaluate((element) => {
    element.scrollTop = element.scrollHeight;
  });
  const mainScrollTop = await main.evaluate((element) => element.scrollTop);
  await page.mouse.wheel(0, 800);
  await expect.poll(() => main.evaluate((element) => element.scrollTop)).toBeGreaterThan(
    mainScrollTop
  );

  for (const tabName of ["Chunk / Citation", "抽出エクスポート"]) {
    await page.getByRole("tab", { name: tabName, exact: false }).click();
    const tabPanelMetrics = await page.getByRole("tabpanel").evaluate((element) => {
      const style = getComputedStyle(element);
      return {
        clientHeight: element.clientHeight,
        overflowY: style.overflowY,
        overscrollBehaviorY: style.overscrollBehaviorY,
      };
    });
    expect(tabPanelMetrics.clientHeight).toBeCloseTo(previewHeight, 0);
    expect(tabPanelMetrics.overflowY).toBe("auto");
    expect(tabPanelMetrics.overscrollBehaviorY).toBe("auto");
  }
});

test("取込解析エンジンは segment parser だけを表示する", async ({
  page,
}) => {
  await mockDocumentWorkspace(page, { pdfPreview: true, mineruSegment: true });

  await page.goto("/documents/doc-1");

  // 原本/取込の処理情報は「取込・診断の詳細」折りたたみ内にあるため展開する。
  await page.getByText("取込・診断の詳細").click();
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
  await expect(page.getByRole("tab", { name: "本文テキスト" })).toHaveAttribute(
    "aria-selected",
    "true"
  );
  await expect(page.getByRole("tab", { name: /Chunk \/ Citation/ })).toBeVisible();
  const panelScrollStyles = await page.getByRole("tabpanel").evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      overflowY: style.overflowY,
      overscrollBehaviorY: style.overscrollBehaviorY,
    };
  });
  expect(panelScrollStyles).toEqual({ overflowY: "visible", overscrollBehaviorY: "auto" });
  await expectNoPageOverflow(page);
});

test("PDF 原本プレビューは左サイドバーを初期表示しない", async ({ page }) => {
  await mockDocumentWorkspace(page, { pdfPreview: true });

  await page.goto("/documents/doc-1");

  const pdfFrame = page.locator('iframe[title="policy.pdf"]');
  await expect(pdfFrame).toHaveAttribute(
    "src",
    /\/api\/documents\/doc-1\/recipes\/recipe-1\/content#page=1&pagemode=none&navpanes=0$/
  );
  await expectNoHorizontalOverflow(page);
});

test("原本プレビューで処理前/処理後を切り替え、ファイル準備から再処理できる", async ({
  page,
}) => {
  const state = await mockDocumentWorkspace(page, { preparedArtifact: true });

  await page.goto("/documents/doc-1");

  const previewPanel = page
    .getByRole("heading", { name: "原本プレビュー" })
    .locator("xpath=ancestor::section[1]");
  await expect(previewPanel.getByRole("button", { name: "処理前" })).toBeVisible();
  await expect(previewPanel.getByRole("button", { name: "処理後" })).toBeEnabled();
  await expect(previewPanel.getByText("経費申請")).toBeVisible();
  await expect(previewPanel.getByRole("link", { name: "ダウンロード" })).toHaveAttribute(
    "href",
    /\/api\/documents\/doc-1\/recipes\/recipe-1\/content\?disposition=attachment$/
  );

  await previewPanel.getByRole("button", { name: "処理後" }).click();

  await expect(previewPanel.getByText("準備後ファイル")).toBeVisible();
  await expect(previewPanel.getByRole("link", { name: "ダウンロード" })).toHaveAttribute(
    "href",
    /\/api\/documents\/doc-1\/recipes\/recipe-1\/content\?variant=prepared&disposition=attachment$/
  );

  await page.getByRole("button", { name: "ファイル準備から再処理" }).click();
  await page.getByRole("button", { name: "再処理する" }).click();

  expect(state.enqueueRequest).toEqual({
    method: "POST",
    path: "/api/documents/doc-1/recipes/recipe-1/ingestion-jobs",
    force: null,
    phase: "PREPROCESS",
  });
  await expectNoPageOverflow(page);
});

test("変換なしでも REVIEW では抽出確認を促す", async ({
  page,
}) => {
  await mockDocumentWorkspace(page, {
    documentStatus: "REVIEW",
    preprocessProfile: "passthrough",
  });

  await page.goto("/documents/doc-1");

  await expect(page.getByText("抽出確認待ち", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "抽出から再処理" })).toHaveCount(0);
  await expectNoPageOverflow(page);
});

test("PREPROCESSED ではファイル準備を確認し、解析へ進む承認を促す", async ({ page }) => {
  const state = await mockDocumentWorkspace(page, {
    documentStatus: "PREPROCESSED",
    preparedArtifact: true,
  });

  await page.goto("/documents/doc-1");

  await expect(page.getByText("ファイル準備確認待ち", { exact: true })).toBeVisible();
  await expect(
    page.getByText("ファイル準備が完了しました。処理後ファイルを確認し、問題なければ解析(抽出)へ進めてください。")
  ).toBeVisible();
  const approve = page.getByRole("button", { name: "承認して解析へ" });
  const actionButtons = approve.locator("xpath=parent::div").getByRole("button");
  await expect(actionButtons.nth(0)).toHaveText("承認して解析へ");
  await expect(actionButtons.nth(1)).toHaveText("ファイル準備から再処理");
  await expect(page.getByRole("button", { name: "却下" })).toHaveCount(0);
  await actionButtons.nth(1).click();
  await page.getByRole("button", { name: "再処理する" }).click();
  await expect.poll(() => state.enqueueRequest).toEqual({
    method: "POST",
    path: "/api/documents/doc-1/recipes/recipe-1/ingestion-jobs",
    force: null,
    phase: "PREPROCESS",
  });
  await expectNoPageOverflow(page);
});

test("CHUNKED では承認と段階別再処理だけを表示する", async ({ page }) => {
  await mockDocumentWorkspace(page, {
    documentStatus: "CHUNKED",
    preparedArtifact: true,
  });

  await page.goto("/documents/doc-1");

  const approve = page.getByRole("button", { name: "承認して Embedding / 索引" });
  const actionButtons = approve.locator("xpath=parent::div").getByRole("button");
  await expect(actionButtons).toHaveText([
    "承認して Embedding / 索引",
    "ファイル準備から再処理",
    "抽出から再処理",
    "Chunk から再処理",
  ]);
  await expect(page.getByRole("button", { name: "却下" })).toHaveCount(0);
  await expectNoPageOverflow(page);
});

test("PREPROCESSED で処理後ファイルが未保存なら危険バナーと再処理導線を出し承認させない", async ({
  page,
}) => {
  await mockDocumentWorkspace(page, {
    documentStatus: "PREPROCESSED",
    brokenPreparedArtifact: true,
  });

  await page.goto("/documents/doc-1");

  await expect(page.getByText("ファイル準備確認待ち", { exact: true })).toBeVisible();
  // 変換成功なのに保存パス欠落 → 危険バナーで明示。
  await expect(
    page.getByText(
      "ファイル準備で変換した処理後ファイルを保存できませんでした。このままでは解析(抽出)へ進めません。ストレージ設定を確認し、ファイル準備を再実行してください。"
    )
  ).toBeVisible();
  // 409 になる承認は出さず、復旧導線(再処理)を出す。
  await expect(page.getByRole("button", { name: "承認して解析へ" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "ファイル準備を再実行" })).toBeVisible();
  await expect(page.getByRole("button", { name: /から再処理/ })).toHaveCount(0);
  // 「処理後」プレビューは保存物が無いので無効。
  await expect(page.getByRole("button", { name: "処理後" })).toBeDisabled();
  await expectNoPageOverflow(page);
});

test("PREPROCESSED で処理後ファイルが欠落(converted以外)でも再処理導線を出し承認させない", async ({
  page,
}) => {
  // preparedArtifact 未指定 = preprocess_artifact が null(passthrough/欠落想定)。
  await mockDocumentWorkspace(page, { documentStatus: "PREPROCESSED" });

  await page.goto("/documents/doc-1");

  await expect(page.getByText("ファイル準備確認待ち", { exact: true })).toBeVisible();
  // converted フラグが無くても「見つからない」旨を明示。
  await expect(
    page.getByText(
      "処理後ファイルが見つかりません。このままでは解析(抽出)へ進めません。ファイル準備を再実行してください。"
    )
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "承認して解析へ" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "ファイル準備を再実行" })).toBeVisible();
  await expect(page.getByRole("button", { name: /から再処理/ })).toHaveCount(0);
  await expectNoPageOverflow(page);
});

test("chunk 取得失敗時は workspace 内にエラー状態を表示する", async ({ page }) => {
  await mockDocumentWorkspace(page, { chunksError: true });

  await page.goto("/documents/doc-1");

  await page.getByRole("tab", { name: /Chunk \/ Citation/ }).click();
  await expect(page.getByText("chunk を取得できません")).toBeVisible();
  await expect(page.getByText("索引状態を確認して再読み込みしてください。")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("画像 preview は同一 surface 上で bbox overlay を位置決めする", async ({ page }) => {
  await mockDocumentWorkspace(page, { imagePreview: true });

  await page.goto("/documents/doc-1");

  await page.getByRole("tab", { name: /Chunk \/ Citation/ }).click();
  const tableChunkButton = page
    .getByRole("tabpanel")
    .getByRole("button", { name: /交通費は1000円/ });
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

  await page.getByRole("tab", { name: /Chunk \/ Citation/ }).click();
  await page.getByRole("tabpanel").getByRole("button", { name: /交通費は1000円/ }).click();

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

  await page.getByRole("tab", { name: /Chunk \/ Citation/ }).click();
  await page.getByRole("tabpanel").getByRole("button", { name: /交通費は1000円/ }).click();

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

  const extractionPanel = page.getByRole("tabpanel");
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

  const extractionPanel = page.getByRole("tabpanel");
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
  // 失敗 code は segment パネルに残す。
  await expect(page.getByText("enterprise_ai_response_validation_error")).toBeVisible();
  // 原因(confidence...)は上部の原因バナーに 1 本化し、diagnostics に埋もれさせない（§9 P2/P3）。
  await expect(page.getByText(/confidence/)).toHaveCount(1);
  await expect(page.getByRole("alert").filter({ hasText: /confidence/ })).toBeVisible();
  // 復旧導線(recovery hint + retry)は segment パネルに残す。
  await expect(
    page.getByText("一時的な応答不整合の可能性があります。再試行すると失敗 segment のみ再処理します。")
  ).toBeVisible();
  await page.getByRole("button", { name: "失敗 segment を再試行" }).click();
  await expect.poll(() => state.retryRequest).toEqual({
    method: "POST",
    path: "/api/documents/doc-1/ingestion-segments/retry",
    recipeId: "recipe-1",
  });
  await expectNoHorizontalOverflow(page);
});

test("文書 workspace はこの文書の取込 job と時間線を表示する", async ({ page }) => {
  await mockDocumentWorkspace(page, {
    documentStatus: "INGESTING",
    latestJobStatus: "RUNNING",
    latestJobStartedAt: new Date(Date.now() - 2_000).toISOString(),
    pdfPreview: true,
  });

  await page.goto("/documents/doc-1");

  const panel = page
    .getByRole("heading", { name: "この文書の取込ジョブ" })
    .locator("xpath=ancestor::section[1]");
  await expect(page.getByText("解析（抽出）中")).toBeVisible();
  await expect(page.getByText("取込中", { exact: true })).toHaveCount(0);
  await expect(panel.getByText("実行中")).toBeVisible();
  await expect(panel.getByText("解析（抽出）", { exact: true })).toBeVisible();
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

test("失敗した取込 job は取込中 banner を残さず原因を表示する", async ({ page }) => {
  await mockDocumentWorkspace(page, {
    documentStatus: "INGESTING",
    latestJobStatus: "FAILED",
    pdfPreview: true,
  });

  await page.goto("/documents/doc-1");

  await expect(
    page.getByText("解析（抽出）を実行しています。完了まで状態を更新します。")
  ).toHaveCount(0);
  const panel = page
    .getByRole("heading", { name: "この文書の取込ジョブ" })
    .locator("xpath=ancestor::section[1]");
  await expect(panel.getByText("失敗", { exact: true })).toBeVisible();
  await expect(panel.getByText("原因")).toBeVisible();
  await expect(panel.getByText("取込処理に失敗しました。")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("同じ取込エラー原因は上部の原因バナーに 1 本化する", async ({ page }) => {
  const message =
    "選択した文書解析サービス（Unlimited-OCR）で解析処理が失敗しました。エラーコード: unlimited_ocr_adapter_failed";
  await mockDocumentWorkspace(page, {
    documentStatus: "ERROR",
    latestJobStatus: "FAILED",
    segmentError: true,
    sharedErrorMessage: message,
  });

  await page.goto("/documents/doc-1");

  // 原因は画面全体で 1 回だけ表示する（§9 P2）。
  await expect(page.getByText(message)).toHaveCount(1);
  // 上部の原因バナー(role=alert)に昇格する。
  await expect(page.getByRole("alert").filter({ hasText: message })).toBeVisible();
  // job パネルでは再掲しない。
  const jobPanel = page
    .getByRole("heading", { name: "この文書の取込ジョブ" })
    .locator("xpath=ancestor::section[1]");
  await expect(jobPanel.getByText(message)).toHaveCount(0);
  // segment の code と復旧導線は残す。
  const segmentPanel = page
    .getByRole("heading", { name: "取込セグメント" })
    .locator("xpath=ancestor::section[1]");
  await expect(segmentPanel.getByText("enterprise_ai_response_validation_error")).toBeVisible();
  await expect(segmentPanel.getByRole("button", { name: "失敗 segment を再試行" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("PDF 抽出中はページ単位の進捗を表示する", async ({ page }) => {
  await mockDocumentWorkspace(page, {
    documentStatus: "INGESTING",
    latestJobStatus: "RUNNING",
    pdfPreview: true,
    progressScenario: "pdf17",
  });

  await page.goto("/documents/doc-1");

  await expect(page.getByText("抽出: 9 / 17 ページ完了")).toBeVisible();
  await expect(page.getByRole("progressbar", { name: "抽出: 9 / 17 ページ完了" })).toBeVisible();
  await expect(page.getByText("p.10-13")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("ページ数が取れない原本は原本全体の解析中として表示する", async ({ page }) => {
  await mockDocumentWorkspace(page, {
    documentStatus: "INGESTING",
    latestJobStatus: "RUNNING",
    progressScenario: "source",
  });

  await page.goto("/documents/doc-1");

  await expect(page.getByText("抽出: 原本全体を解析中")).toBeVisible();
  await expect(page.getByRole("progressbar", { name: "抽出: 原本全体を解析中" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("ファイル準備の開始 message を操作欄に表示し、本文 export と chunk を自動更新する", async ({
  page,
}) => {
  await mockDocumentWorkspace(page, {
    autoRefreshAfterEnqueue: true,
    documentStatus: "UPLOADED",
  });

  await page.goto("/documents/doc-1");

  // Chunk / エクスポートは初期状態では空。
  await page.getByRole("tab", { name: /Chunk \/ Citation/ }).click();
  await expect(page.getByText("chunk はまだ作成されていません。")).toBeVisible();
  await page.getByRole("tab", { name: "抽出エクスポート" }).click();
  await expect(page.getByText("表示できる抽出エクスポートはありません。")).toBeVisible();

  await page.getByRole("button", { name: "ファイル準備を実行" }).click();
  const actionStatus = page.getByText(
    "ファイル準備を開始しました。完了まで状態を更新します。"
  );
  await expect(actionStatus).toBeVisible();
  await expect(page.getByText(/取込ジョブをキューに投入/)).toHaveCount(0);
  await expect(actionStatus.locator("xpath=ancestor::div[contains(@class, 'border-t')][1]")).toBeVisible();

  // 取込後、エクスポート(現在のタブ)が自動更新される。
  await expect(page.getByText("<!-- page: 1 -->")).toBeVisible({ timeout: 9_000 });
  // Chunk タブにも反映される。
  await page.getByRole("tab", { name: /Chunk \/ Citation/ }).click();
  await expect(page.getByRole("button", { name: /経費申請の概要です。/ })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("バックグラウンド失敗後は開始 message を消し、失敗原因と単一の再実行だけを残す", async ({
  page,
}) => {
  await mockDocumentWorkspace(page, { backgroundFailureAfterEnqueue: true });

  await page.goto("/documents/doc-1");
  await page.getByRole("button", { name: "ファイル準備を実行" }).click();
  const startedMessage = page.getByText(
    "ファイル準備を開始しました。完了まで状態を更新します。"
  );
  await expect(startedMessage).toBeVisible();

  await expect(page.getByRole("alert").filter({ hasText: "取込処理に失敗しました。" })).toBeVisible({
    timeout: 9_000,
  });
  await expect(startedMessage).toHaveCount(0);
  await expect(page.getByText("取込処理に失敗しました。")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "ファイル準備を再実行" })).toBeVisible();
  await expect(page.getByRole("button", { name: /から再処理/ })).toHaveCount(0);
  await expectNoPageOverflow(page);
});

test("ERROR は失敗 phase と前段の再処理ボタンを表示する", async ({ page }) => {
  await mockDocumentWorkspace(page, {
    documentStatus: "ERROR",
    latestJobStatus: "FAILED",
    latestJobPhase: "CHUNK",
    preparedArtifact: true,
  });

  await page.goto("/documents/doc-1");

  await expect(page.getByRole("button", { name: "Chunk 作成を再実行" })).toBeVisible();
  await expect(page.getByRole("button", { name: "ファイル準備から再処理" })).toBeVisible();
  await expect(page.getByRole("button", { name: "抽出から再処理" })).toBeVisible();
  await expect(page.getByRole("button", { name: "ファイル準備を再実行" })).toHaveCount(0);
  await expectNoPageOverflow(page);
});

test("ERROR は前提 artifact が無ければファイル準備の再実行だけに戻す", async ({ page }) => {
  await mockDocumentWorkspace(page, {
    documentStatus: "ERROR",
    latestJobStatus: "FAILED",
    latestJobPhase: "EXTRACT",
  });

  await page.goto("/documents/doc-1");

  await expect(page.getByRole("button", { name: "ファイル準備を再実行" })).toBeVisible();
  await expect(page.getByRole("button", { name: "抽出を再実行" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /から再処理/ })).toHaveCount(0);
  await expectNoPageOverflow(page);
});

test("重複文書は重複元を表示し、明示操作では recipe のファイル準備を投入する", async ({ page }) => {
  const state = await mockDocumentWorkspace(page, {
    chunksEmpty: true,
    duplicate: true,
    documentStatus: "UPLOADED",
  });

  await page.goto("/documents/doc-1");

  await expect(
    page.getByText(/同一内容の文書が既に登録されています。重複元: original\.pdf \/ 索引済み/)
  ).toBeVisible();
  await expect(page.getByText("内容が同じでも別文書として処理したい場合")).toBeVisible();
  await page.getByRole("button", { name: "重複を無視してファイル準備" }).click();
  await expect.poll(() => state.enqueueRequest).toMatchObject({
    path: "/api/documents/doc-1/recipes/recipe-1/ingestion-jobs",
    phase: "PREPROCESS",
  });
  await expectNoHorizontalOverflow(page);
});

test("取込セグメントが多い場合は高さ固定で内部スクロールする", async ({ page }) => {
  await mockDocumentWorkspace(page, { segmentCount: 30 });

  await page.goto("/documents/doc-1");

  // 取込セグメントは「取込・診断の詳細」折りたたみ内にあるため展開する。
  await page.getByText("取込・診断の詳細").click();
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
    latestJobPhase?: "PREPROCESS" | "EXTRACT" | "CHUNK" | "INDEX";
    latestJobStartedAt?: string;
    autoRefreshAfterEnqueue?: boolean;
    backgroundFailureAfterEnqueue?: boolean;
    documentStatus?: string;
    duplicate?: boolean;
    progressScenario?: "pdf17" | "source";
    preparedArtifact?: boolean;
    documentOnlyPreparedArtifact?: boolean;
    brokenPreparedArtifact?: boolean;
    preprocessProfile?: "passthrough" | "text_normalize";
    sharedErrorMessage?: string;
  } = {}
) {
  const state: {
    retryRequest: { method: string; path: string; recipeId: string | null } | null;
    enqueueRequest: {
      method: string;
      path: string;
      force: string | null;
      phase: string | null;
    } | null;
    enqueued: boolean;
    backgroundJobPolls: number;
    backgroundFailed: boolean;
    extractionExportRequests: number;
  } = {
    retryRequest: null,
    enqueueRequest: null,
    enqueued: false,
    backgroundJobPolls: 0,
    backgroundFailed: false,
    extractionExportRequests: 0,
  };
  const segmentFailureMessage =
    "OCI Enterprise AI VLM response が StructuredExtraction schema と一致しません。失敗項目: confidence: less_than_equal。";
  await page.route("**/api/documents/doc-1/ingestion-config", async (route) => {
    await route.fulfill({
      json: {
        data: {
          document_id: "doc-1",
          is_indexed: (options.documentStatus ?? "INDEXED") === "INDEXED",
          owning_knowledge_base: null,
          effective_preprocess_profile: options.preprocessProfile ?? "text_normalize",
          effective_chunking_strategy: "recursive",
          effective_parser_adapter_backend: "local",
          observed_chunking_strategy: "recursive",
          observed_parser_backend: "local_partition",
          chunking_drift: false,
          parser_drift: false,
          config_drift: false,
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await page.route("**/api/documents/doc-1", async (route) => {
    await route.fulfill({
      json: {
        data: documentDetail({
          imagePreview: options.imagePreview,
          pdfPreview: options.pdfPreview,
          status: currentDocumentStatus(options, state),
          duplicate: options.duplicate,
          preparedArtifact: options.preparedArtifact || options.documentOnlyPreparedArtifact,
          brokenPreparedArtifact: options.brokenPreparedArtifact,
          errorMessage:
            options.sharedErrorMessage ?? (options.segmentError ? segmentFailureMessage : undefined),
        }),
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await page.route("**/api/documents/doc-1/recipes", async (route) => {
    const status = currentDocumentStatus(options, state);
    const detail = documentDetail({
      imagePreview: options.imagePreview,
      pdfPreview: options.pdfPreview,
      status,
      preparedArtifact: options.preparedArtifact || options.segmentError,
      brokenPreparedArtifact: options.brokenPreparedArtifact,
      errorMessage:
        options.sharedErrorMessage ?? (options.segmentError ? segmentFailureMessage : undefined),
    });
    const hasExtraction = ["REVIEW", "CHUNKED", "INDEXED"].includes(status) ||
      (status === "ERROR" && ["CHUNK", "INDEX"].includes(options.latestJobPhase ?? ""));
    await route.fulfill({
      json: {
        data: [
          {
            recipe_id: "recipe-1",
            document_id: "doc-1",
            slot_no: 1,
            status,
            failed_phase: status === "ERROR" ? (options.latestJobPhase ?? "PREPROCESS") : null,
            processing_config: {},
            effective_processing_config: {},
            preprocess_artifact: detail.preprocess_artifact,
            active_extraction_recipe_id: hasExtraction ? "er-recipe-1-r1" : null,
            active_chunk_set_id: status === "INDEXED" ? "chunk-set-recipe-1" : null,
            chunk_count: status === "INDEXED" ? 2 : 0,
            vector_count: status === "INDEXED" ? 2 : 0,
            config_revision: 1,
            materialized_revision: status === "INDEXED" ? 1 : null,
            searchable: status === "INDEXED",
            needs_reprocessing: false,
            error_message:
              status === "ERROR"
                ? (options.sharedErrorMessage ??
                  (options.segmentError ? segmentFailureMessage : "取込処理に失敗しました。"))
                : null,
            steps: recipeSteps(status, options.latestJobPhase),
            created_at: "2026-06-15T00:00:00Z",
            updated_at: "2026-06-15T00:00:20Z",
            started_at: null,
            finished_at: status === "INDEXED" || status === "ERROR" ? "2026-06-15T00:00:20Z" : null,
          },
        ],
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await page.route("**/api/documents/doc-1/recipes/recipe-1/ingestion-jobs**", async (route) => {
    const url = new URL(route.request().url());
    state.enqueueRequest = {
      method: route.request().method(),
      path: url.pathname,
      force: url.searchParams.get("force"),
      phase: url.searchParams.get("phase"),
    };
    state.enqueued = true;
    await route.fulfill({
      json: {
        data: ingestionJob("QUEUED", {
          phase: (url.searchParams.get("phase") as "PREPROCESS" | "EXTRACT" | "CHUNK" | "INDEX" | null) ?? "PREPROCESS",
        }),
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await page.route("**/api/documents/doc-1/recipes/recipe-1/approve", async (route) => {
    const status = currentDocumentStatus(options, state);
    const phase = status === "PREPROCESSED" ? "EXTRACT" : status === "CHUNKED" ? "INDEX" : "CHUNK";
    await route.fulfill({
      json: {
        data: ingestionJob("QUEUED", { phase }),
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await page.route("**/api/documents/doc-1/ingestion-segments/retry**", async (route) => {
    const url = new URL(route.request().url());
    state.retryRequest = {
      method: route.request().method(),
      path: url.pathname,
      recipeId: url.searchParams.get("recipe_id"),
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
      const url = new URL(route.request().url());
      state.enqueueRequest = {
        method: route.request().method(),
        path: url.pathname,
        force: url.searchParams.get("force"),
        phase: url.searchParams.get("phase"),
      };
      state.enqueued = true;
      await route.fulfill({
        json: {
          data: ingestionJob("QUEUED", {
            phase:
              (url.searchParams.get("phase") as
                | "PREPROCESS"
                | "EXTRACT"
                | "CHUNK"
                | "INDEX"
                | null) ?? "PREPROCESS",
          }),
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }
    await route.fulfill({
      json: {
        data: [
          ingestionJob(
            options.backgroundFailureAfterEnqueue && state.backgroundFailed
              ? "FAILED"
              : options.latestJobStatus ?? "SUCCEEDED",
            {
            startedAt: options.latestJobStartedAt,
            errorMessage: options.sharedErrorMessage,
              phase: options.backgroundFailureAfterEnqueue
                ? "PREPROCESS"
                : options.latestJobPhase,
            }
          ),
        ],
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await page.route("**/api/documents/ingestion-jobs/job-retry-segments", async (route) => {
    if (options.backgroundFailureAfterEnqueue) {
      state.backgroundFailed = state.backgroundJobPolls > 0;
      state.backgroundJobPolls += 1;
    }
    await route.fulfill({
      json: {
        data: ingestionJob(
          options.backgroundFailureAfterEnqueue && state.backgroundFailed ? "FAILED" : "QUEUED",
          {
          phase:
            (state.enqueueRequest?.phase as
              | "PREPROCESS"
              | "EXTRACT"
              | "CHUNK"
              | "INDEX"
              | null) ?? "EXTRACT",
          }
        ),
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
  await page.route(
    /\/api\/documents\/doc-1(?:\/recipes\/recipe-1)?\/content/,
    async (route) => {
    const variant = new URL(route.request().url()).searchParams.get("variant");
    if (variant === "prepared") {
      await route.fulfill({
        status: 200,
        headers: { "content-type": "text/plain; charset=utf-8" },
        body: "準備後ファイル\n交通費は1000円です。",
      });
      return;
    }
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
    }
  );
  await page.route(
    /\/api\/documents\/doc-1(?:\/recipes\/recipe-1)?\/chunks(?:\?|$)/,
    async (route) => {
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
    }
  );
  await page.route("**/api/documents/doc-1/chunk-sets", async (route) => {
    await route.fulfill({
      json: {
        data: [],
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await page.route(
    /\/api\/documents\/doc-1(?:\/recipes\/recipe-1)?\/extraction-export/,
    async (route) => {
    state.extractionExportRequests += 1;
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
    }
  );
  await page.route("**/api/documents/doc-1/ingestion-segments", async (route) => {
    const failedSegment = {
      segment_id: "doc-1:p1-10",
      document_id: "doc-1",
      recipe_id: "recipe-1",
      status: "FAILED",
      parser_backend: "enterprise_ai",
      parser_profile: "enterprise_ai_pdf_layout",
      page_start: 1,
      page_end: 10,
      attempt_count: 2,
      artifact_path: null,
      error_code: "enterprise_ai_response_validation_error",
      error_message:
        options.sharedErrorMessage ??
        segmentFailureMessage,
    };
    await route.fulfill({
      json: {
        data: options.segmentError
          ? [failedSegment]
          : ingestionSegments(options.segmentCount ?? 1, {
              mineru: options.mineruSegment,
              progressScenario: options.progressScenario,
            }),
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  return state;
}

function currentDocumentStatus(
  options: {
    backgroundFailureAfterEnqueue?: boolean;
    documentStatus?: string;
    segmentError?: boolean;
    autoRefreshAfterEnqueue?: boolean;
  },
  state: { backgroundFailed: boolean; enqueued: boolean }
) {
  if (options.backgroundFailureAfterEnqueue) {
    return state.backgroundFailed ? "ERROR" : "UPLOADED";
  }
  if (options.autoRefreshAfterEnqueue) {
    return state.enqueued ? "INDEXED" : (options.documentStatus ?? "ERROR");
  }
  return options.documentStatus ??
    (options.segmentError || (options.autoRefreshAfterEnqueue && !state.enqueued)
      ? "ERROR"
      : "INDEXED");
}

function recipeSteps(
  status: string,
  failedPhase: "PREPROCESS" | "EXTRACT" | "CHUNK" | "INDEX" | undefined
) {
  const phases = ["PREPROCESS", "EXTRACT", "CHUNK", "INDEX"] as const;
  const completedCount: Record<string, number> = {
    UPLOADED: 0,
    PREPROCESSING: 0,
    PREPROCESSED: 1,
    INGESTING: 1,
    REVIEW: 2,
    CHUNKING: 2,
    CHUNKED: 3,
    INDEXING: 3,
    INDEXED: 4,
  };
  const runningPhase: Record<string, (typeof phases)[number]> = {
    PREPROCESSING: "PREPROCESS",
    INGESTING: "EXTRACT",
    CHUNKING: "CHUNK",
    INDEXING: "INDEX",
  };
  const failedIndex = status === "ERROR" ? phases.indexOf(failedPhase ?? "PREPROCESS") : -1;
  return phases.map((phase, index) => ({
    phase,
    status:
      index < (completedCount[status] ?? failedIndex)
        ? "SUCCEEDED"
        : runningPhase[status] === phase
          ? "RUNNING"
          : failedIndex === index
            ? "FAILED"
            : "PENDING",
    started_at: null,
    finished_at: null,
    error_message: failedIndex === index ? "取込処理に失敗しました。" : null,
  }));
}

function ingestionSegments(
  count: number,
  options: { mineru?: boolean; progressScenario?: "pdf17" | "source" } = {}
) {
  if (options.progressScenario === "source") {
    return [
      {
        segment_id: "doc-1:source",
        document_id: "doc-1",
        recipe_id: "recipe-1",
        status: "RUNNING",
        parser_backend: "local_partition",
        parser_profile: "local_text_structure",
        page_start: null,
        page_end: null,
        progress_unit: "source",
        progress_start: null,
        progress_end: null,
        attempt_count: 1,
        artifact_path: null,
        error_code: null,
        error_message: null,
      },
    ];
  }
  if (options.progressScenario === "pdf17") {
    const ranges: Array<[string, "SUCCEEDED" | "RUNNING" | "QUEUED", number, number]> = [
      ["1-5", "SUCCEEDED", 1, 5],
      ["6-9", "SUCCEEDED", 6, 9],
      ["10-13", "RUNNING", 10, 13],
      ["14-17", "QUEUED", 14, 17],
    ];
    return ranges.map(([range, status, start, end]) => ({
      segment_id: `doc-1:p${range}`,
      document_id: "doc-1",
      recipe_id: "recipe-1",
      status,
      parser_backend: "enterprise_ai",
      parser_profile: "enterprise_ai_pdf_layout",
      page_start: start,
      page_end: end,
      progress_unit: "page",
      progress_start: start,
      progress_end: end,
      attempt_count: status === "QUEUED" ? 0 : 1,
      artifact_path: status === "SUCCEEDED" ? `local://doc-1/${range}` : null,
      error_code: null,
      error_message: null,
    }));
  }
  return Array.from({ length: count }, (_, index) => {
    const start = index * 10 + 1;
    const end = start + 9;
    const status = index < 4 ? "SUCCEEDED" : index === 4 ? "RUNNING" : "QUEUED";
    const mineru = Boolean(options.mineru);
    return {
      segment_id: `doc-1:p${start}-${end}`,
      document_id: "doc-1",
      recipe_id: "recipe-1",
      status,
      parser_backend: mineru ? "mineru" : index === 0 ? "local_partition" : "enterprise_ai",
      parser_profile: mineru
        ? "mineru_adapter"
        : index === 0
          ? "local_text_structure"
          : "enterprise_ai_pdf_layout",
      page_start: start,
      page_end: end,
      progress_unit: "page",
      progress_start: start,
      progress_end: end,
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
  const payload = documentDetail().extraction;
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
  options: {
    queuedAt?: string;
    startedAt?: string;
    finishedAt?: string;
    errorMessage?: string;
    phase?: "PREPROCESS" | "EXTRACT" | "CHUNK" | "INDEX";
  } = {}
) {
  return {
    id: status === "RUNNING" ? "job-running-0001" : "job-retry-segments",
    document_id: "doc-1",
    recipe_id: "recipe-1",
    recipe_revision: 1,
    status,
    phase: options.phase ?? "EXTRACT",
    parser_profile: "enterprise_ai_pdf_layout",
    quality_warnings: [],
    skip_reason: null,
    error_message: status === "FAILED" ? options.errorMessage ?? "取込処理に失敗しました。" : null,
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
  options: {
    duplicate?: boolean;
    imagePreview?: boolean;
    pdfPreview?: boolean;
    status?: string;
    preparedArtifact?: boolean;
    brokenPreparedArtifact?: boolean;
    errorMessage?: string;
  } = {}
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

  const duplicateOfDocumentId = options.duplicate ? "doc-original" : null;
  return {
    id: "doc-1",
    file_name: fileName,
    status: options.status ?? "INDEXED",
    category_name: null,
    content_type: contentType,
    file_size_bytes: 64,
    content_sha256: "a".repeat(64),
    duplicate_of_document_id: duplicateOfDocumentId,
    uploaded_at: "2026-06-15T00:00:00Z",
    indexed_at: "2026-06-15T00:00:03Z",
    object_storage_path: `local://${fileName}`,
    preprocess_artifact:
      options.preparedArtifact || options.brokenPreparedArtifact
        ? {
            derivation_id: "prepared-1",
            profile: options.brokenPreparedArtifact ? "pdf_to_page_images" : "text_normalize",
            converted: true,
            converter_name: options.brokenPreparedArtifact ? "pdf_to_page_images" : "text_normalize",
            converter_version: "v1",
            source_content_type: contentType,
            source_sha256: "a".repeat(64),
            // 壊れ状態: 変換は成功(converted=true)したが保存パスが欠落している。
            object_storage_path: options.brokenPreparedArtifact
              ? null
              : "local://policy__prepared.txt",
            content_type: "text/plain",
            sha256: "b".repeat(64),
            file_name: "policy__prepared.txt",
            page_map: {},
            warnings: [],
          }
        : null,
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
    error_message: options.errorMessage ?? null,
    duplicate_source: options.duplicate
      ? {
          id: "doc-original",
          file_name: "original.pdf",
          status: "INDEXED",
          uploaded_at: "2026-06-14T00:00:00Z",
          indexed_at: "2026-06-14T00:02:00Z",
        }
      : null,
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
      duplicate_of_document_id: duplicateOfDocumentId,
      unsupported_reason: null,
      quality_status: options.duplicate ? "warning" : "ready",
      quality_warnings: options.duplicate ? ["duplicate_content"] : [],
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
