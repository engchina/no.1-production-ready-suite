import { expect, type Page, test } from "./fixtures/test";
import {
  expectMainScrollEndsAtContent,
  expectNoPageOverflow,
  mockAuthUser,
  mockDatabaseReady,
  mockLocalAuth,
} from "./_helpers";
import { expectMainActionsClearOfToast } from "./_toast-placement";

function ok(json: unknown) {
  return { data: json, error_messages: [], warning_messages: [] };
}

const adapterConfig = {
  version: 1,
  ingestion: {
    preprocess_profile: null,
    parser_adapter_backend: null,
    parser_docling_enabled: null,
    parser_unstructured_enabled: null,
    chunking_strategy: null,
    chunk_size: null,
    chunk_overlap: null,
    chunk_min_chars: null,
    chunk_context_header_enabled: null,
    graph_profile: null,
    field_extraction_enabled: null,
    vision_enabled: null,
    navigation_summary_enabled: null,
    auto_parse_after_preprocess_enabled: null,
    auto_chunk_after_extract_enabled: null,
    auto_index_after_chunk_enabled: null,
  },
  query: {
    guardrail_policy: null,
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
  };
}

// SSE 応答(stage→metadata→citations→delta→done)。streamSearch は \n\n 区切りで解析する。
function searchStreamBody(
  fileName = "policy.pdf",
  citationCount = 1,
  metadata: Record<string, unknown> = {},
) {
  const citations = Array.from({ length: citationCount }, (_, index) => ({
    document_id: `doc-${index + 1}`,
    chunk_id: `doc-${index + 1}:cs_1:0`,
    text: index === 0 ? "就業規則の根拠テキスト" : `補足の根拠テキスト ${index + 1}`,
    score: 0.91,
    rerank_score: 0.82,
    file_name: index === 0 ? fileName : `policy-${index + 1}.pdf`,
    category_name: null,
    metadata,
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
  await page.route("**/api/knowledge-bases/kb-1", (route) =>
    route.fulfill({ json: ok(kbDetail(indexedDocumentCount)) })
  );
  // KB の抽出する項目（#548）。上の wildcard の一覧を返さない。
  await page.route("**/api/knowledge-bases/kb-1/extraction-fields", (route) =>
    route.fulfill({ json: ok({ inherits_default: true, fields: [] }) })
  );
}

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
});

test("KB の長い質問は3行で折り返し、Enterでは検索せずボタンで1回だけ実行する", { tag: "@responsive" }, async ({ page }, testInfo) => {
  await mockKbPage(page, 1);
  const requests: Record<string, unknown>[] = [];
  await page.route("**/api/search/stream", route => {
    requests.push(route.request().postDataJSON());
    return route.fulfill({ status: 200, contentType: "text/event-stream", body: searchStreamBody() });
  });
  await page.goto("/knowledge-bases/kb-1");
  const input = page.getByPlaceholder("このナレッジベースに質問してみる…");
  await expect(input).toHaveJSProperty("tagName", "TEXTAREA");
  await expect(input).toHaveAttribute("rows", "3");
  const firstLine = "合成の対象について、比較条件と確認する項目を詳しく説明してください。".repeat(6);
  await input.fill(firstLine);
  await input.press("End");
  await input.press("Enter");
  await input.pressSequentially("合成の末尾。");
  const question = firstLine + "\n合成の末尾。";
  await expect(input).toHaveValue(question);
  expect(requests).toEqual([]);
  const scroll = await input.evaluate(el => ({ wraps: getComputedStyle(el).whiteSpace, horizontal: el.scrollWidth <= el.clientWidth + 1, vertical: el.scrollHeight > el.clientHeight, resize: getComputedStyle(el).resize }));
  expect(scroll.horizontal).toBe(true);
  expect(scroll.vertical).toBe(true);
  expect(scroll.resize).toBe("vertical");
  await input.evaluate(el => { el.scrollTop = el.scrollHeight; });
  await expectNoPageOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("kb-long-question.png") });
  await page.getByRole("button", { name: "検索テスト", exact: true }).click();
  await expect.poll(() => requests.length).toBe(1);
  expect(requests[0]).toMatchObject({ query: question });
});

test("KB 詳細の検索テストで検索・回答プロファイル無しに回答と引用を確認できる", async ({ page }) => {
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

  await page.getByPlaceholder("このナレッジベースに質問してみる…").fill("有給休暇の付与日数は？");
  await page.getByRole("button", { name: "検索テスト" }).click();

  // 回答と引用(原本ファイル名)が表示される。
  await expect(page.getByText("これはテスト回答です。")).toBeVisible();
  await expect(page.getByText("policy.pdf")).toBeVisible();
  await expect(page.getByRole("meter", { name: /取得スコア/ })).toHaveCount(0);
  await expect(page.getByRole("meter", { name: "Rerank スコア: 0.820" })).toBeVisible();

  // request は単一 KB scope を明示し、検索・回答プロファイルは渡さない。回答は作らずに検索だけを頼む（#593）。
  expect(streamRequestBody).toMatchObject({ knowledge_base_ids: ["kb-1"], retrieval_only: true });
  expect(streamRequestBody).not.toHaveProperty("search_answer_profile_id");
  // 検索の方式は回答エンジンが使わないため送らず、選ぶチップも出さない（#595）。
  expect(streamRequestBody).not.toHaveProperty("mode");
  await expect(page.getByRole("button", { name: "ハイブリッド" })).toHaveCount(0);

  // 引用カードは画面を移動するリンクを持たない（#442）。
  await expect(page.getByRole("link", { name: /引用位置/ })).toHaveCount(0);

  // 引用箇所は画面に留まったまま右のサイドパネルで確認する（#442 / #2360）。文書の詳細へのリンクは出さない（#2462）。
  // 回答の根拠なので、見出しは「回答の根拠」。
  const opener = page.getByRole("button", { name: "policy.pdf の根拠を表示" });
  await opener.click();
  const dialog = page.getByRole("dialog", { name: "回答の根拠" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByTestId("evidence-source-summary").getByTestId("evidence-file-name")).toHaveText("policy.pdf");
  await expect(dialog.getByRole("link", { name: "文書の詳細を別タブで開く" })).toHaveCount(0);
  await dialog.getByRole("button", { name: "閉じる" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  // 閉じたら開いた操作へフォーカスを戻す。
  await expect(opener).toBeFocused();

  await expectNoPageOverflow(page);
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile", width: 375, height: 900 },
]) {
  for (const theme of ["light", "dark"] as const) {
    test(`検索だけの結果（回答なし）は件数と時間と引用を出す (#593, ${viewport.name}, ${theme})`, async ({
      page,
    }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      // 外観（テーマ）は共有 UI の ui-store の保存値で決まる。
      await page.addInitScript(
        (value) =>
          window.localStorage.setItem(
            "production-ready-rag.ui",
            JSON.stringify({ state: { theme: value }, version: 0 })
          ),
        theme
      );
      await mockKbPage(page, 1);
      // 回答エンジンが検索だけを行ったときの応答（delta の本文が空）。
      const body = searchStreamBody("policy.pdf", 2).replace(
        'event: delta\ndata: {"text":"これはテスト回答です。"}',
        'event: delta\ndata: {"text":""}'
      );
      await page.route("**/api/search/stream", (route) =>
        route.fulfill({ status: 200, contentType: "text/event-stream", body })
      );

      await page.goto("/knowledge-bases/kb-1");
      await expect
        .poll(() => page.evaluate(() => document.documentElement.classList.contains("dark")))
        .toBe(theme === "dark");
      await page.getByPlaceholder("このナレッジベースに質問してみる…").fill("有給休暇の付与日数は？");
      await page.getByRole("button", { name: "検索テスト" }).click();

      await expect(page.getByTestId("kb-search-test-meta")).toHaveText("2 件 / 120 ms");
      await expect(page.getByText("policy.pdf")).toBeVisible();
      await expect(page.getByRole("heading", { name: "検索結果（2）" })).toBeVisible();
      // 回答の欄は出さない（空の回答の枠を残さない）。
      await expect(page.getByRole("heading", { name: "回答", exact: true })).toHaveCount(0);
      await expectNoPageOverflow(page);
      // 検索だけの結果の箇所は、パネルの見出しを「検索で見つかった箇所」にする（#2360）。
      await page.getByRole("button", { name: /の根拠を表示$/ }).first().click();
      const sheet = page.getByRole("dialog", { name: "検索で見つかった箇所" });
      await expect(sheet).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(sheet).toBeHidden();
    });
  }
}

// #349: 回答の引用のプレビューでも、要素の表示領域（要素ごとの bbox）を強調し、1 画面分の高さで表示する。
test("引用プレビューは PDF のページ画像に根拠の要素を強調し、1 画面分の高さで表示する", { tag: "@responsive" }, async ({
  page,
}, testInfo) => {
  await mockKbPage(page, 1);
  await page.route(/\/api\/documents\/doc-1\/preview-pages(?:\?|$)/, (route) =>
    route.fulfill({
      json: ok({ page_count: 1, pages: [{ page_number: 1, width: 612, height: 792 }] }),
    })
  );
  // ページの画像は読み込み中の表示を確かめるまで返さない（#2474）。
  let releaseImage = () => {};
  const imageHeld = new Promise<void>((resolve) => {
    releaseImage = resolve;
  });
  await page.route(/\/api\/documents\/doc-1\/preview-pages\/1(?:\?|$)/, async (route) => {
    await imageHeld;
    await route.fulfill({
      status: 200,
      headers: { "content-type": "image/svg+xml" },
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="612" height="792"><rect width="612" height="792" fill="white"/></svg>',
    });
  });
  const body = searchStreamBody("policy.pdf").replace(
    '"metadata":{}',
    `"metadata":${JSON.stringify({
      page_start: 1,
      page_width: 1224,
      page_height: 1584,
      bbox: "[122.4, 158.4, 1101.6, 792]",
      bbox_unit: "absolute",
      engine_metadata_json: JSON.stringify({
        layout: {
          display_regions: [
            {
              page: 1,
              boxes: [
                { record_id: "docling-p1-1", seq_no: 1, category: "Text", bbox: [122.4, 158.4, 612, 316.8] },
                { record_id: "docling-p1-2", seq_no: 2, category: "Text", bbox: [612, 633.6, 1101.6, 792] },
              ],
            },
          ],
        },
      }),
    })}`
  );
  await page.route("**/api/search/stream", (route) =>
    route.fulfill({ status: 200, contentType: "text/event-stream", body })
  );

  await page.goto("/knowledge-bases/kb-1");
  await page.getByPlaceholder("このナレッジベースに質問してみる…").fill("有給休暇の付与日数は？");
  await page.getByRole("button", { name: "検索テスト" }).click();
  await page.getByRole("button", { name: /の根拠を表示$/ }).click();

  const dialog = page.getByRole("dialog");
  await expect(dialog.getByTestId("preview-viewer")).toBeVisible();
  // 読み込みが終わるまでは描きかけの画像を隠し、灰色の面と読み込み中の表示を出す（#2474）。
  await expect(dialog.getByTestId("preview-page-loading")).toContainText("ページを読み込んでいます");
  await expect(dialog.getByTestId("preview-page-skeleton")).toBeVisible();
  // 読み込み中はほかの領域と同じく、領域（ビューポート）の先頭に全幅の行で出す（ページの上に浮かせない。#2510）。
  const viewportBox = await dialog.getByTestId("preview-viewport").boundingBox();
  const loadingRowBox = await dialog.getByTestId("preview-page-loading").locator("xpath=..").boundingBox();
  expect(Math.abs(loadingRowBox!.y - viewportBox!.y)).toBeLessThanOrEqual(1);
  expect(Math.abs(loadingRowBox!.x - viewportBox!.x)).toBeLessThanOrEqual(1);
  expect(loadingRowBox!.width).toBeGreaterThanOrEqual(viewportBox!.width - 20);
  await expect(dialog.getByTestId("preview-image-surface").locator("img")).toHaveCSS("opacity", "0");
  await expect(dialog.getByTestId("bbox-content-overlay")).toHaveCount(0);
  releaseImage();
  await expect(dialog.getByTestId("preview-page-loading")).toHaveCount(0);
  await expect(dialog.getByTestId("preview-page-skeleton")).toHaveCount(0);
  await expect(dialog.getByTestId("preview-image-surface").locator("img")).toHaveCSS("opacity", "1");
  const overlays = dialog.getByTestId("bbox-content-overlay");
  await expect(overlays).toHaveCount(2);
  await expect(dialog.getByText("p.1 の 2 か所を強調しています")).toBeVisible();
  // 解析時のページ画像 px（1224x1584）をページに対する % に直して重ねる: 10% / 10% / 40% / 10%。
  const surfaceBox = await dialog.getByTestId("preview-image-surface").boundingBox();
  const overlayBox = await overlays.first().boundingBox();
  expect((overlayBox!.x - surfaceBox!.x) / surfaceBox!.width).toBeCloseTo(0.1, 2);
  expect((overlayBox!.y - surfaceBox!.y) / surfaceBox!.height).toBeCloseTo(0.1, 2);
  expect(overlayBox!.width / surfaceBox!.width).toBeCloseTo(0.4, 2);
  expect(overlayBox!.height / surfaceBox!.height).toBeCloseTo(0.1, 2);

  // 右のサイドパネル（#2360）は画面の高さいっぱいで右端に付く。広い画面では本文が左に残り、狭い画面
  // （sm 未満）は全画面のシート。プレビューは要約の下にある。
  const viewport = page.viewportSize()!;
  // 右から滑り出し切るのを待つ。
  await expect
    .poll(async () => {
      const box = await dialog.boundingBox();
      return box ? Math.round(box.x + box.width) : -1;
    })
    .toBe(viewport.width);
  const dialogBox = await dialog.boundingBox();
  expect(dialogBox!.height).toBeCloseTo(viewport.height, 0);
  expect(dialogBox!.x + dialogBox!.width).toBeCloseTo(viewport.width, 0);
  if (viewport.width < 640) expect(dialogBox!.width).toBeCloseTo(viewport.width, 0);
  else expect(dialogBox!.width).toBeLessThan(viewport.width - 100);
  const summaryBox = await dialog.getByTestId("evidence-source-summary").boundingBox();
  const previewBox = await dialog.getByTestId("evidence-source-preview").boundingBox();
  expect(previewBox!.y).toBeGreaterThan(summaryBox!.y + summaryBox!.height - 1);
  await page.screenshot({ path: testInfo.outputPath(`citation-source-sheet-${testInfo.project.name}.png`) });
  // Escape で閉じる。
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expectNoPageOverflow(page);
});

test("KB 検索テストの引用が内部スクロールしてもページ末尾に空白を作らない", { tag: "@responsive" }, async ({ page }) => {
  await mockKbPage(page, 1);
  await page.route("**/api/search/stream", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: searchStreamBody("policy.pdf", 8),
    })
  );

  await page.goto("/knowledge-bases/kb-1");
  await page.getByPlaceholder("このナレッジベースに質問してみる…").fill("有給休暇の付与日数は？");
  await page.getByRole("button", { name: "検索テスト" }).click();

  const citationList = page.locator("ul.bounded-scroll-area-lg");
  await expect(citationList.locator(":scope > li")).toHaveCount(8);
  await expect
    .poll(() => citationList.evaluate((element) => element.scrollHeight > element.clientHeight))
    .toBe(true);
  await expectMainScrollEndsAtContent(page);
});

// #4560: KB 詳細の末尾の「検索テスト」も、通知（Toast）の決まった置き場所と重ならない（#1728 の規則）。
// 未実行の案内はボタンの上に出し、ボタンの下に高い空の状態を置かない。
test("KB 詳細の主操作と末尾の検索テストは通知の置き場所と重ならない（#4560）", { tag: "@responsive" }, async ({ page }, testInfo) => {
  await mockKbPage(page, 1);
  await page.goto("/knowledge-bases/kb-1");
  await expect(page.getByRole("button", { name: "検索テスト" })).toBeVisible();
  await expect(page.getByText("質問を入力すると、このナレッジベースから上位の根拠チャンクを取得します。")).toBeVisible();
  await expectMainActionsClearOfToast(page);
  await page.screenshot({ path: testInfo.outputPath("kb-detail-end.png") });
});

test("索引済み文書が無い KB は検索テストを促す空状態を出す", async ({ page }) => {
  await mockKbPage(page, 0);

  await page.goto("/knowledge-bases/kb-1");

  await expect(page.getByText("索引済みの文書がありません。")).toBeVisible();
  // 索引前は入力欄を出さない。
  await expect(page.getByPlaceholder("このナレッジベースに質問してみる…")).toHaveCount(0);

  await expectNoPageOverflow(page);
});

// #1210: 原本が保存先に無いとき（保存先の変更・削除）、プレビューに backend の理由と対処を出し、再試行できる。
const MISSING_ORIGINAL = "原本ファイルが保存先にありません。文書をアップロードし直してください。";
const missingOriginal = {
  status: 404,
  json: {
    data: null,
    error_messages: [MISSING_ORIGINAL],
    warning_messages: [],
    error_code: "RAG_DOCUMENT_FILE_MISSING",
  },
};

for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`引用本文の取得失敗は理由と対処を出し、再試行できる (${viewport.name})`, { tag: "@responsive" }, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockKbPage(page, 1);
    let restored = false;
    let originalRequests = 0;
    const message = "根拠を閲覧できません。";
    await page.route(/\/api\/documents\/doc-1\/content(?:\?|$)/, route => { originalRequests++; return route.fulfill(missingOriginal); });
    await page.route(/\/api\/documents\/doc-1\/reader(?:\?|$)/, route => restored
      ? route.fulfill({json:{data:{revision:"v1",anchor_status:"exact",section_path:[],scope:"paragraph",blocks:[{id:"e1",text:"合成の引用本文",offset:0,highlights:[],cited:true}],previous_cursor:null,next_cursor:null,outline:[]}}})
      : route.fulfill({status:404,json:{data:null,error_messages:[message],warning_messages:[]}}));
    await page.route("**/api/search/stream", route => route.fulfill({status:200,contentType:"text/event-stream",body:searchStreamBody("文書 A.md")}));
    await page.goto("/knowledge-bases/kb-1");
    await page.getByPlaceholder("このナレッジベースに質問してみる…").fill("合成の質問");
    await page.getByRole("button", { name: "検索テスト" }).click();
    await page.getByRole("button", { name: /の根拠を表示$/ }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByText(message,{exact:true})).toBeVisible();
    await expect(dialog.getByText("詳細", { exact: true })).toBeVisible();
    await expectNoPageOverflow(page);
    restored = true;
    await dialog.getByRole("button", { name: "再試行",exact:true }).click();
    await expect(dialog.getByTestId("evidence-reader-content")).toContainText("合成の引用本文");
    await expect(dialog.getByText(message,{exact:true})).toHaveCount(0);
    expect(originalRequests).toBe(0);
  });
}

test("原本が無い PDF の引用プレビューは 404 の本文を iframe に出さず、理由と対処を出す", async ({
  page,
}) => {
  await mockKbPage(page, 1);
  await page.route(/\/api\/documents\/doc-1\/preview-pages(?:\?|$)/, (route) =>
    route.fulfill(missingOriginal)
  );
  await page.route("**/api/search/stream", (route) =>
    route.fulfill({ status: 200, contentType: "text/event-stream", body: searchStreamBody("policy.pdf") })
  );

  await page.goto("/knowledge-bases/kb-1");
  await page.getByPlaceholder("このナレッジベースに質問してみる…").fill("有給休暇の付与日数は？");
  await page.getByRole("button", { name: "検索テスト" }).click();
  await page.getByRole("button", { name: /の根拠を表示$/ }).click();

  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText(MISSING_ORIGINAL)).toBeVisible();
  await expect(dialog.locator("iframe")).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "再試行" })).toBeVisible();
});

// 切替の後も、引用した版のファイルの頁を出す（#4168）。
async function mockVersionedOfficeCitation(page: Page): Promise<void> {
  await mockKbPage(page, 1);
  const preparedArtifact = {
    file_name: "policy.pdf",
    content_type: "application/pdf",
    object_storage_path: "artifacts/canonical/doc-1/trace/canonical.pdf",
  };
  await page.route("**/api/documents/doc-1/recipes/recipe-1/preview-source?chunk_set_id=cs-cited", route => route.fulfill({ json: ok({ file_name: "sample.pdf", content_type: "application/pdf", is_pdf: true }) }));
  await page.route("**/api/documents/doc-1", (route) =>
    route.fulfill({ json: ok({ id: "doc-1", preprocess_artifact: null }) })
  );
  await page.route("**/api/documents/doc-1/recipes", (route) =>
    route.fulfill({ json: ok([{ recipe_id: "recipe-1", status: "INDEXED", steps: [], preprocess_artifact: preparedArtifact }]) })
  );
  await page.route("**/api/search/stream", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: searchStreamBody("policy.docx", 1, {
        recipe_id: "recipe-1",
        chunk_set_id: "cs-cited",
      }),
    })
  );
}

async function openCitation(page: Page) {
  await page.goto("/knowledge-bases/kb-1");
  await page.getByPlaceholder("このナレッジベースに質問してみる…").fill("有給休暇の付与日数は？");
  await page.getByRole("button", { name: "検索テスト" }).click();
  await page.getByRole("button", { name: /の根拠を表示$/ }).click();
  return page.getByRole("dialog");
}

test("引用プレビューの頁は引用した版のファイルを開く", { tag: "@responsive" }, async ({ page }) => {
  const requested: string[] = [];
  await mockVersionedOfficeCitation(page);
  await page.route(/\/api\/documents\/doc-1\/recipes\/recipe-1\/preview-pages\?/, (route) => {
    requested.push(route.request().url());
    return route.fulfill({
      json: ok({ page_count: 1, pages: [{ page_number: 1, width: 612, height: 792 }] }),
    });
  });
  await page.route(/\/api\/documents\/doc-1\/recipes\/recipe-1\/preview-pages\/1\?/, (route) => {
    requested.push(route.request().url());
    return route.fulfill({
      contentType: "image/png",
      body: Buffer.from(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=",
        "base64"
      ),
    });
  });

  const dialog = await openCitation(page);

  await expect(dialog.locator("iframe")).toHaveCount(0);
  await expect.poll(() => requested.length).toBeGreaterThanOrEqual(2);
  for (const url of requested) {
    const params = new URL(url).searchParams;
    expect(params.get("chunk_set_id")).toBe("cs-cited");
    expect(params.get("variant")).toBe("prepared");
  }
  await expectNoPageOverflow(page);
});

test("消えた版の引用プレビューは iframe や再試行に戻さず、版の失効を案内する", { tag: "@responsive" }, async ({ page }) => {
  await mockVersionedOfficeCitation(page);
  await page.route(/\/api\/documents\/doc-1\/recipes\/recipe-1\/preview-pages\?/, (route) =>
    route.fulfill({
      status: 409,
      json: {
        data: null,
        error_messages: ["資料の版が変わりました。"],
        warning_messages: [],
        error_code: "source_stale",
      },
    })
  );

  const dialog = await openCitation(page);

  await expect(dialog.getByTestId("source-version-stale-notice")).toBeVisible();
  await expect(dialog.locator("iframe")).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "再試行", exact: true })).toHaveCount(0);
  await expectNoPageOverflow(page);
});

test("Office 引用プレビューの降格表示では原本をダウンロードできる", async ({ page }) => {
  await mockKbPage(page, 1);
  await page.route("**/api/documents/doc-1/recipes/recipe-1/preview-source?chunk_set_id=cs-cited", route => route.fulfill({ json: ok({ file_name: "sample.docx", content_type: "application/octet-stream", is_pdf: false }) }));
  await page.route("**/api/search/stream", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: searchStreamBody("sample.docx", 1, { recipe_id: "recipe-1", chunk_set_id: "cs-cited" }),
    })
  );

  await page.goto("/knowledge-bases/kb-1");
  await page.getByPlaceholder("このナレッジベースに質問してみる…").fill("有給休暇の付与日数は？");
  await page.getByRole("button", { name: "検索テスト" }).click();
  await page.getByRole("button", { name: /の根拠を表示$/ }).click();

  const dialog = page.getByRole("dialog");
  // 表示できない形式の案内は形式によらず共通の 1 文（#2679）。
  await expect(
    dialog.getByText("このファイル形式はプレビューに対応していません。", { exact: true })
  ).toBeVisible();
  await expect(
    dialog.getByRole("link", { name: "ファイルをダウンロード", exact: true })
  ).toHaveAttribute("href", /\/api\/documents\/doc-1\/recipes\/recipe-1\/content\?disposition=attachment&chunk_set_id=cs-cited$/);
});

// #303: 文書の詳細はワークスペース（アップロード・文書インデックス）専用の API を使う。KB の権限だけの
// 利用者には、所属文書・引用から詳細へのリンクを出さない（押すと 403 で権限なしの画面へ飛ぶため）。
test("文書の詳細を開けない利用者には、所属文書と引用から詳細へのリンクを出さない", async ({ page }) => {
  await mockKbPage(page, 1);
  await mockAuthUser(page, {
    permissions: ["menu.knowledge_bases"],
    allowed_knowledge_base_ids: ["kb-1"],
  });
  // 所属文書は `GET /api/documents?knowledge_base_id=kb-1`。ほかの文書 API は前の route へ回す。
  await page.route("**/api/documents**", (route) => {
    const url = new URL(route.request().url());
    if (url.pathname !== "/api/documents" || url.searchParams.get("knowledge_base_id") !== "kb-1") {
      return route.fallback();
    }
    return route.fulfill({
      json: ok({
        items: [
          {
            id: "doc-1",
            file_name: "policy.pdf",
            status: "INDEXED",
            category_name: null,
            content_type: "application/pdf",
            file_size_bytes: 1024,
            content_sha256: null,
            duplicate_of_document_id: null,
            uploaded_at: "2026-06-15T00:00:00Z",
            indexed_at: "2026-06-15T00:05:00Z",
            knowledge_bases: [{ id: "kb-1", name: "社内規程" }],
            source_profile: null,
          },
        ],
        total: 1,
        limit: 10,
        offset: 0,
        has_next: false,
      }),
    });
  });
  await page.route("**/api/search/stream", (route) =>
    route.fulfill({ status: 200, contentType: "text/event-stream", body: searchStreamBody() })
  );

  await page.goto("/knowledge-bases/kb-1");

  // 所属文書の名前は表示するが、リンクにしない。
  const member = page.locator("li").filter({ hasText: "policy.pdf" }).first();
  await expect(member).toBeVisible();
  await expect(member.getByRole("link")).toHaveCount(0);

  await page.getByPlaceholder("このナレッジベースに質問してみる…").fill("有給休暇の付与日数は？");
  await page.getByRole("button", { name: "検索テスト" }).click();
  await expect(page.getByText("これはテスト回答です。")).toBeVisible();

  // 引用は画面内のダイアログだけを出し、文書の詳細へのリンクは出さない。
  await page.getByRole("button", { name: "policy.pdf の根拠を表示" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("link", { name: "文書の詳細を別タブで開く" })).toHaveCount(0);
  await dialog.getByRole("button", { name: "閉じる" }).click();
  await expect(page).toHaveURL(/\/knowledge-bases\/kb-1$/);
  await expectNoPageOverflow(page);
});

test("検索テストと停止は同じボタンで、フォーカスを保ったまま切り替わる（#413）", { tag: "@responsive" }, async ({ page }) => {
  await mockKbPage(page, 1);
  let calls = 0;
  let aborted = 0;
  page.on("requestfailed", (request) => {
    if (request.url().includes("/api/search/stream")) aborted += 1;
  });
  // 応答を返さず、生成中のままにする。
  await page.route("**/api/search/stream", () => {
    calls += 1;
    return new Promise<void>(() => undefined);
  });

  await page.goto("/knowledge-bases/kb-1");
  const input = page.getByPlaceholder("このナレッジベースに質問してみる…");
  await input.fill("有給休暇の付与日数は？");
  const button = page.getByTestId("kb-search-test-run-stop");
  await expect(button).toHaveAccessibleName("検索テスト");
  const idleBox = await button.boundingBox();

  await button.click();
  await expect(button).toHaveAccessibleName("停止");
  await expect(button).toBeFocused();
  // 「検索テスト」→「停止」でも幅が縮まず、位置が変わらない。
  const runningBox = await button.boundingBox();
  expect(runningBox?.x).toBe(idleBox?.x);
  expect(runningBox?.width).toBe(idleBox?.width);
  // 処理中は結果の領域に今の工程と経過時間を出す（ボタンはスピナーを出さない）。
  await expect(page.getByTestId("kb-search-test-progress")).toBeVisible();

  // 実行中の入力欄の Enter では停止しない。
  await input.press("Enter");
  await page.waitForTimeout(300);
  await expect(button).toHaveAccessibleName("停止");
  expect(calls).toBe(1);
  expect(aborted).toBe(0);

  await button.click();
  await expect(button).toHaveAccessibleName("検索テスト");
  await expect(button).toBeFocused();
  await expect.poll(() => aborted).toBe(1);
  // 停止したことを結果の領域に残す（検索だけで回答の枠が無いときも、何も出さずに空白にしない）。
  await expect(page.getByTestId("kb-search-test-cancelled")).toHaveText(
    "検索は途中で停止されました。必要に応じて再検索してください。"
  );

  // 停止の後に再び実行できる。ダブルクリックの 2 回目（実行中に変わった直後の「停止」）では停止しない。
  await button.dblclick();
  await expect.poll(() => calls).toBe(2);
  await expect(button).toHaveAccessibleName("停止");
  await page.waitForTimeout(300);
  await expect(button).toHaveAccessibleName("停止");
  expect(aborted).toBe(1);
  await expectNoPageOverflow(page);
});


// 同じファイル名でも引用CSの原本と現在のレシピは異なる。
for (const scenario of [
  { id: "old-pdf-current-missing", citedPdf: true, current: null, filename: "sample.docx", pdf: true },
  { id: "old-pdf-current-jsonl", citedPdf: true, current: "jsonl", filename: "sample.docx", pdf: true },
  { id: "old-native-current-pdf", citedPdf: false, current: "pdf", filename: "sample.docx", pdf: false },
  { id: "same-pdf", citedPdf: true, current: "pdf", filename: "sample.docx", pdf: true },
  { id: "original-pdf", citedPdf: false, current: null, filename: "sample.pdf", pdf: true },
]) {
  test(`引用版で表示種類を決める ${scenario.id}`, { tag: "@responsive" }, async ({ page }) => {
    await mockVersionedOfficeCitation(page);
    await page.route("**/api/documents/doc-1/recipes", route => route.fulfill({ json: ok([{
      recipe_id: "recipe-1", status: "INDEXED", steps: [],
      preprocess_artifact: scenario.current ? { file_name: `sample.${scenario.current}`, content_type: scenario.current === "pdf" ? "application/pdf" : "application/x-ndjson", object_storage_path: `prepared/sample.${scenario.current}` } : null,
    }]) }));
    await page.route("**/api/search/stream", route => route.fulfill({ contentType: "text/event-stream", body: searchStreamBody(scenario.filename, 1, { recipe_id: "recipe-1", chunk_set_id: "cs-cited" }) }));
    await page.route("**/api/documents/doc-1/recipes/recipe-1/preview-source?chunk_set_id=cs-cited", route => route.fulfill({ json: ok({ file_name: scenario.citedPdf ? "sample.pdf" : scenario.filename, content_type: scenario.citedPdf ? "application/pdf" : "application/vnd.openxmlformats-officedocument.wordprocessingml.document", is_pdf: scenario.citedPdf }) }));
    await page.route(/\/api\/documents\/doc-1\/recipes\/recipe-1\/preview-pages\?/, route => route.fulfill({ json: ok({ page_count: 1, pages: [{ page_number: 1, width: 612, height: 792 }] }) }));
    await page.route(/\/api\/documents\/doc-1\/recipes\/recipe-1\/preview-pages\/1\?/, route => route.fulfill({ contentType: "image/png", body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=", "base64") }));
    const dialog = await openCitation(page);
    if (scenario.pdf) {
      await expect(dialog.locator('img[src*="/preview-pages/1?"]')).toBeVisible();
    } else {
      await expect(dialog.getByText("このファイル形式はプレビューに対応していません。")).toBeVisible();
      await expect(dialog.locator('img[src*="/preview-pages/1?"]')).toHaveCount(0);
    }
    await expectNoPageOverflow(page);
    if (process.env.CITATION_PREVIEW_SCREENSHOTS) await page.screenshot({ path: `${process.env.CITATION_PREVIEW_SCREENSHOTS}/${scenario.id}-${test.info().project.name}.png` });
  });
}

for (const failure of ["missing-scope", "missing-source", "stale"] as const) {
  test(`引用版の種類が未確認なら現在版へ戻さない ${failure}`, { tag: "@responsive" }, async ({ page }) => {
    await mockVersionedOfficeCitation(page);
    if (failure === "missing-scope") {
      await page.route("**/api/search/stream", route => route.fulfill({ contentType: "text/event-stream", body: searchStreamBody("sample.docx", 1, { recipe_id: "recipe-1" }) }));
    } else {
      await page.route("**/api/documents/doc-1/recipes/recipe-1/preview-source?chunk_set_id=cs-cited", route => route.fulfill({
        status: failure === "stale" ? 409 : 404,
        json: { data: null, error_messages: [failure === "stale" ? "資料の版が変わりました。" : "この版が解析したファイルの記録がありません。"], warning_messages: [], error_code: failure === "stale" ? "source_stale" : "document_file_missing" },
      }));
    }
    const dialog = await openCitation(page);
    if (failure === "stale") {
      await expect(dialog.getByTestId("source-version-stale-notice")).toBeVisible();
      await expect(dialog.getByRole("button", { name: "再試行", exact: true })).toHaveCount(0);
    } else {
      await expect(dialog.getByText(failure === "missing-scope" ? "引用した版のファイルの記録がありません。" : "この版が解析したファイルの記録がありません。", { exact: true })).toBeVisible();
    }
    await expect(dialog.locator('img[src*="/preview-pages/1?"]')).toHaveCount(0);
    await expect(dialog.locator("iframe")).toHaveCount(0);
    await expectNoPageOverflow(page);
  });
}
