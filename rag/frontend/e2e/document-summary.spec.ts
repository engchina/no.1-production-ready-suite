import { expect, type Page, test } from "./fixtures/test";
import { expectNoPageOverflow, mockDatabaseReady, mockLocalAuth } from "./_helpers";

// Issue 4793: 文書の詳細の「文書の要約」。取込の後に 1 文書 1 回の LLM で作った説明を出し、
// まだ無い・失敗した要約は「要約を作る」「作り直す」で作れる（内容は合成）。

const DOC_ID = "doc-summary";
const SUMMARY_URL = `**/api/documents/${DOC_ID}/summary`;
const ok = (data: unknown) => ({ json: { data, error_messages: [], warning_messages: [] } });

const READY = {
  status: "ready",
  generating: false,
  text: "架空の装置 A の点検の手順書です。月に 1 回の点検の項目と、結果の記録の方法を定めています。",
  topics: ["架空の装置 A", "点検の手順", "記録の方法"],
  input_truncated: true,
  failure_code: null,
  generated_at: "2026-11-10T03:00:00Z",
};

function recipe(indexed: boolean) {
  const status = indexed ? "SUCCEEDED" : "PENDING";
  return {
    recipe_id: "recipe-1",
    document_id: DOC_ID,
    slot_no: 1,
    status: indexed ? "INDEXED" : "UPLOADED",
    failed_phase: null,
    processing_config: {},
    effective_processing_config: {},
    preprocess_artifact: null,
    // 抽出の確認の欄は対象外なので、抽出の版は持たせない。
    active_extraction_recipe_id: null,
    active_chunk_set_id: indexed ? "chunks-1" : null,
    chunk_count: indexed ? 4 : 0,
    vector_count: indexed ? 4 : 0,
    config_revision: 1,
    materialized_revision: indexed ? 1 : null,
    searchable: indexed,
    needs_reprocessing: false,
    error_message: null,
    steps: ["PREPROCESS", "EXTRACT", "CHUNK", "INDEX"].map((phase) => ({
      phase,
      status,
      started_at: null,
      finished_at: null,
      error_message: null,
    })),
    created_at: "2026-11-10T00:00:00Z",
    updated_at: "2026-11-10T00:00:05Z",
    started_at: null,
    finished_at: null,
  };
}

async function openDocument(page: Page, { indexed = true }: { indexed?: boolean } = {}) {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
  await page.route("**/api/knowledge-bases**", (route) =>
    route.fulfill(ok({ items: [], total: 0, limit: 100, offset: 0, has_next: false }))
  );
  await page.route(`**/api/documents/${DOC_ID}/**`, (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/summary")) return route.fallback();
    if (path.endsWith("/recipes")) return route.fulfill(ok([recipe(indexed)]));
    if (path.includes("/content")) {
      return route.fulfill({ status: 200, contentType: "text/plain", body: "合成の本文" });
    }
    return route.fulfill(ok([]));
  });
  await page.route(`**/api/documents/${DOC_ID}`, (route) =>
    route.fulfill(
      ok({
        id: DOC_ID,
        file_name: "sample-procedure.txt",
        status: indexed ? "INDEXED" : "UPLOADED",
        category_name: null,
        content_type: "text/plain",
        file_size_bytes: 64,
        content_sha256: "a".repeat(64),
        duplicate_of_document_id: null,
        uploaded_at: "2026-11-10T00:00:00Z",
        indexed_at: indexed ? "2026-11-10T00:00:05Z" : null,
        object_storage_path: "local://sample-procedure.txt",
        error_message: null,
        knowledge_bases: [],
        source_profile: null,
      })
    )
  );
  await page.goto(`/documents/${DOC_ID}`);
  return page.getByTestId("document-summary");
}

test("作成済みの要約は、文・話題の語・作成日時・切り詰めの注記を出す", { tag: "@responsive" }, async ({ page }, testInfo) => {
  await page.route(SUMMARY_URL, (route) => route.fulfill(ok({ enabled: true, summary: READY })));
  const panel = await openDocument(page);

  await expect(panel.getByRole("heading", { name: "文書の要約", exact: true })).toBeVisible();
  await expect(panel.getByText("原文ではありません", { exact: false })).toBeVisible();
  await expect(panel.getByTestId("document-summary-text")).toHaveText(READY.text);
  await expect(panel.getByRole("list", { name: "主な話題", exact: true }).getByRole("listitem")).toHaveText(READY.topics);
  await expect(panel.getByText("長い文書のため、先頭の本文と見出しの一覧から作りました。", { exact: false })).toBeVisible();
  await expect(panel.getByRole("button", { name: "作り直す", exact: true })).toBeEnabled();
  await expectNoPageOverflow(page);
  await panel.scrollIntoViewIfNeeded();
  await panel.screenshot({ path: testInfo.outputPath(`document-summary-ready-${testInfo.project.name}.png`) });
});

test("まだ無い要約は「要約を作る」で作り、作成中と結果を同じ節に出す", { tag: "@responsive" }, async ({ page }) => {
  let state: Record<string, unknown> = { enabled: true, summary: null };
  let release: () => void = () => {};
  const posted: string[] = [];
  await page.route(SUMMARY_URL, async (route) => {
    if (route.request().method() === "POST") {
      posted.push(route.request().url());
      await new Promise<void>((resolve) => (release = resolve));
      state = { enabled: true, summary: { ...READY, input_truncated: false } };
    }
    await route.fulfill(ok(state));
  });
  const panel = await openDocument(page);

  await expect(panel.getByTestId("document-summary-empty")).toHaveText(
    "この文書の要約はまだありません。「要約を作る」で作成できます。"
  );
  await panel.getByRole("button", { name: "要約を作る", exact: true }).click();
  // 作成中は節の中に 1 つだけ進行を出し、ボタンは押せない。まだ無い旨の文は消す。
  await expect(panel.getByTestId("document-summary-generating")).toBeVisible();
  await expect(panel.getByTestId("document-summary-empty")).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "要約を作る", exact: true })).toBeDisabled();
  await expectNoPageOverflow(page);
  release();
  await expect(panel.getByTestId("document-summary-text")).toHaveText(READY.text);
  await expect(panel.getByTestId("document-summary-generating")).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "作り直す", exact: true })).toBeEnabled();
  expect(posted).toHaveLength(1);
});

test("失敗した要約は理由と作り直しを示し、作成中は読み直して結果に替わる", { tag: "@responsive" }, async ({ page }, testInfo) => {
  let state: Record<string, unknown> = {
    enabled: true,
    summary: { ...READY, status: "failed", text: null, topics: [], input_truncated: false, failure_code: "timeout", generated_at: null },
  };
  await page.route(SUMMARY_URL, async (route) => {
    if (route.request().method() === "POST") {
      // 作り直しは受け付け、結果は読み直しで届く（別の画面・取込の後段が作っている途中と同じ状態）。
      state = { enabled: true, summary: { ...READY, status: "pending", generating: true, text: null, topics: [], generated_at: null } };
      await route.fulfill(ok(state));
      state = { enabled: true, summary: READY };
      return;
    }
    await route.fulfill(ok(state));
  });
  const panel = await openDocument(page);

  const failure = panel.getByRole("status").or(panel.getByRole("alert")).filter({ hasText: "文書の要約を作成できませんでした" });
  await expect(failure).toContainText("時間の上限までに作成が終わりませんでした。");
  await expect(failure).toContainText("取込は完了しています。「作り直す」でもう一度作成できます。");
  await panel.screenshot({ path: testInfo.outputPath(`document-summary-failed-${testInfo.project.name}.png`) });
  await panel.getByRole("button", { name: "作り直す", exact: true }).click();
  // 作成中の応答の後は、定期の読み直しで結果に替わる。
  await expect(panel.getByTestId("document-summary-text")).toHaveText(READY.text, { timeout: 10_000 });
  await expect(failure).toHaveCount(0);
  await expectNoPageOverflow(page);
});

test("無効の設定と取込の前の文書では、作る操作を出さずに理由を示す", async ({ page }) => {
  await page.route(SUMMARY_URL, (route) => route.fulfill(ok({ enabled: false, summary: null })));
  let panel = await openDocument(page);
  await expect(panel.getByTestId("document-summary-empty")).toContainText("文書の要約は無効です。");
  await expect(panel.getByRole("button")).toHaveCount(0);

  await page.unroute(SUMMARY_URL);
  await page.route(SUMMARY_URL, (route) => route.fulfill(ok({ enabled: true, summary: null })));
  panel = await openDocument(page, { indexed: false });
  await expect(panel.getByTestId("document-summary-empty")).toHaveText(
    "この文書の要約はまだありません。取込が終わると作成します。"
  );
  await expect(panel.getByRole("button")).toHaveCount(0);
});

test("要約を読み込めないときは、節の中で失敗と再試行を出す", async ({ page }) => {
  let fail = true;
  await page.route(SUMMARY_URL, (route) =>
    fail
      ? route.fulfill({ status: 500, json: { data: null, error_messages: ["合成の読み込みの失敗"], warning_messages: [] } })
      : route.fulfill(ok({ enabled: true, summary: READY }))
  );
  const panel = await openDocument(page);
  const retry = panel.getByRole("button", { name: "再試行", exact: true });
  await expect(retry).toBeVisible({ timeout: 15_000 });
  fail = false;
  await retry.click();
  await expect(panel.getByTestId("document-summary-text")).toHaveText(READY.text);
});
