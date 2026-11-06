import { expect, type Page, test } from "./fixtures/test";
import { expectNoPageOverflow, mockDatabaseReady, mockLocalAuth } from "./_helpers";

// Issue 281: 文書の詳細（DocumentWorkspace）の不具合の回帰。
// - ERROR の文書で「再実行」の投入に失敗したとき、何も表示されなかった
// - 進行中ジョブを観測していない 409 が、表示した瞬間に消えていた
// - 存在しない文書を開くと既定の再試行で数秒 Skeleton のままだった

const DOC_ID = "doc-detail-review";

function documentDetail(status: string) {
  return {
    id: DOC_ID,
    file_name: "policy.txt",
    status,
    category_name: null,
    content_type: "text/plain",
    file_size_bytes: 64,
    content_sha256: "a".repeat(64),
    duplicate_of_document_id: null,
    uploaded_at: "2026-06-18T00:00:00Z",
    indexed_at: null,
    object_storage_path: "local://policy.txt",
    error_message: status === "ERROR" ? "ファイル準備に失敗しました。" : null,
    extraction: null,
    knowledge_bases: [],
    source_profile: null,
  };
}

function recipeView(status: string) {
  const errored = status === "ERROR";
  return {
    recipe_id: "recipe-1",
    document_id: DOC_ID,
    slot_no: 1 as const,
    status,
    failed_phase: errored ? "PREPROCESS" : null,
    processing_config: {},
    effective_processing_config: {},
    preprocess_artifact: null,
    active_extraction_recipe_id: null,
    active_chunk_set_id: null,
    chunk_count: 0,
    vector_count: 0,
    config_revision: 1,
    materialized_revision: null,
    searchable: false,
    needs_reprocessing: false,
    error_message: errored ? "ファイル準備に失敗しました。" : null,
    steps: [
      {
        phase: "PREPROCESS",
        status: errored ? "FAILED" : "PENDING",
        started_at: null,
        finished_at: null,
        error_message: errored ? "ファイル準備に失敗しました。" : null,
      },
      { phase: "EXTRACT", status: "PENDING", started_at: null, finished_at: null, error_message: null },
      { phase: "CHUNK", status: "PENDING", started_at: null, finished_at: null, error_message: null },
      { phase: "INDEX", status: "PENDING", started_at: null, finished_at: null, error_message: null },
    ],
    created_at: "2026-06-18T00:00:00Z",
    updated_at: "2026-06-18T00:00:05Z",
    started_at: null,
    finished_at: null,
  };
}

function failedJob() {
  return {
    id: "job-failed",
    document_id: DOC_ID,
    recipe_id: "recipe-1",
    recipe_revision: 1,
    status: "FAILED",
    phase: "PREPROCESS",
    parser_profile: "local_text_structure",
    quality_warnings: [],
    skip_reason: null,
    error_message: "ファイル準備に失敗しました。",
    attempt_count: 1,
    max_attempts: 3,
    queued_at: "2026-06-18T00:00:00Z",
    started_at: "2026-06-18T00:00:01Z",
    finished_at: "2026-06-18T00:00:02Z",
  };
}

const ok = (data: unknown) => ({ json: { data, error_messages: [], warning_messages: [] } });

async function mockWorkspace(page: Page, status: string, enqueueError: string) {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
  await page.route("**/api/knowledge-bases**", (route) =>
    route.fulfill(ok({ items: [], total: 0, limit: 100, offset: 0, has_next: false }))
  );
  await page.route(`**/api/documents/${DOC_ID}/**`, (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/recipes")) return route.fulfill(ok([recipeView(status)]));
    if (path.endsWith("/recipes/recipe-1/ingestion-jobs")) {
      return route.fulfill({
        status: 409,
        json: { data: null, error_messages: [enqueueError], warning_messages: [] },
      });
    }
    if (path.endsWith("/ingestion-jobs")) {
      return route.fulfill(ok(status === "ERROR" ? [failedJob()] : []));
    }
    if (path.endsWith("/knowledge-bases")) return route.fulfill(ok([]));
    if (path.includes("/content")) {
      return route.fulfill({ status: 200, contentType: "text/plain", body: "経費申請" });
    }
    return route.fulfill(ok([]));
  });
  await page.route(`**/api/documents/${DOC_ID}`, (route) =>
    route.fulfill(ok(documentDetail(status)))
  );
}

test("ERROR の文書で再実行の投入に失敗したら、ボタンの近くに理由を出す", async ({ page }) => {
  await mockWorkspace(page, "ERROR", "このレシピは処理中または待機中です。");
  await page.goto(`/documents/${DOC_ID}`);

  await page.getByRole("button", { name: "ファイル準備を再実行", exact: true }).first().click();
  await page.getByRole("alertdialog").getByRole("button", { name: "再実行する" }).click();

  await expect(
    page.getByText("ファイル準備を開始できませんでした。 このレシピは処理中または待機中です。")
  ).toBeVisible();
});

test("進行中ジョブの無い 409 は表示したまま残す", async ({ page }) => {
  await mockWorkspace(page, "UPLOADED", "レシピ設定が更新されました。再読み込みしてください。");
  await page.goto(`/documents/${DOC_ID}`);

  await page.getByRole("button", { name: "ファイル準備を実行", exact: true }).first().click();

  const message = page.getByText(
    "ファイル準備を開始できませんでした。 レシピ設定が更新されました。再読み込みしてください。"
  );
  await expect(message).toBeVisible();
  // 以前は進行中ジョブが無いため 1 フレームで消えていた。
  await page.waitForTimeout(1_000);
  await expect(message).toBeVisible();
});

test("存在しない文書は再試行せずにすぐ「見つかりません」を出す", async ({ page }) => {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
  let detailCalls = 0;
  await page.route("**/api/documents/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/documents/missing-doc") detailCalls += 1;
    return route.fulfill({
      status: 404,
      json: { data: null, error_messages: ["ドキュメントが見つかりません。"], warning_messages: [] },
    });
  });

  await page.goto("/documents/missing-doc");

  // 見つからないときも見出し（PageHeader）を先に出し、本文だけを「対象が見つかりません」にする
  // （ナレッジベース・検索・回答プロファイルと共有の EditorTargetState。#581）。
  await expect(page.getByText("対象が見つかりません")).toBeVisible({ timeout: 2_500 });
  await expect(page.getByText("「missing-doc」は削除されたか、存在しません。")).toBeVisible();
  await expect(page.getByRole("heading", { name: "文書インデックス", level: 1 })).toBeVisible();
  expect(detailCalls).toBe(1);
  await page.getByRole("main").getByRole("button", { name: "一覧へ戻る" }).click();
  await expect(page).toHaveURL(/\/file-list$/);
});

// #581 / #618: 文書詳細の見出しは、ナレッジベース・検索・回答プロファイルの詳細と同じ PageHeader（左上の一覧へ戻る・状態）。
test("文書詳細の見出しは PageHeader に一覧へ戻る（左上）・ファイル名・状態を出す", async ({ page }) => {
  await mockWorkspace(page, "ERROR", "このレシピは処理中または待機中です。");
  await page.route((url) => url.pathname === "/api/documents", (route) =>
    route.fulfill(ok({ items: [], total: 0, limit: 50, offset: 0, has_next: false }))
  );
  await page.goto(`/documents/${DOC_ID}`);

  const header = page.locator("header[data-page-header]");
  await expect(header.getByRole("heading", { name: "policy.txt", level: 1 })).toBeVisible();
  await expect(header.getByTestId("editor-back")).toHaveAccessibleName("文書インデックスの一覧へ戻る");
  // 状態はレシピの状態（色だけでなくアイコンと文言）。本文のカードにファイル名と状態を重ねない。
  await expect(header.getByText("エラー", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "policy.txt" })).toHaveCount(1);
  // 本文（処理レシピ・操作）はそのまま。
  await expect(page.getByRole("button", { name: "ファイル準備を再実行", exact: true }).first()).toBeVisible();

  await header.getByTestId("editor-back").click();
  await expect(page).toHaveURL(/\/file-list$/);
});


test("索引の使用版・構築中の対象版を区別し、固定した公開版で再処理を確認する", { tag: "@responsive" }, async ({ page }) => {
  await mockWorkspace(page, "INDEXED", "言語資源の現在版が変わりました。再確認してください。");
  const recipe = { ...recipeView("INDEXED"), active_chunk_set_id: "chunks-old", searchable: true };
  const used = { recipe_id: "recipe-1", chunk_set_id: "chunks-old", release_id: "release-1", version: 1, ingest_hash: "hash-old", relation: "different", built_at: "2026-10-17T00:00:00Z", job_id: "job-old", target_release_id: "release-2", target_version: 2 };
  await page.route(`**/api/documents/${DOC_ID}/recipes`, route => route.fulfill(ok([recipe])));
  await page.route(`**/api/documents/${DOC_ID}`, route => route.fulfill(ok({ ...documentDetail("INDEXED"), file_name: "sample.txt", language_pack: { current_release_id: "release-2", current_version: 2, recipes: [used] } })));
  await page.route("**/api/settings/language-pack/releases/release-1", route => route.fulfill(ok({ id: "release-1", version: 1, effective: { numbers: { article_units: ["条"] } }, hash: "old", ingest_hash: "old", published_at: "2026-10-17T00:00:00Z", published_by: null, previous_id: null, restored_from: null, change_note: "" })));
  await page.route("**/api/settings/language-pack/releases/compare?*", route => route.fulfill(ok({ before_id: "release-1", after_id: "release-2", differences: [{ section: "numbers", key: "article_units", before: ["条"], after: ["条", "款"], ingest: true }], ingest_changed: true, impact_requires_review: true, affected_stages: [] })));
  let submitted: URL | null = null;
  await page.route(`**/api/documents/${DOC_ID}/recipes/recipe-1/ingestion-jobs?*`, async route => {
    submitted = new URL(route.request().url());
    await route.fulfill({ status: 409, json: { data: null, error_messages: ["言語資源の現在版が変わりました。再確認してください。"], warning_messages: [] } });
  });
  await page.goto(`/documents/${DOC_ID}`);
  const panel = page.getByTestId("document-language-pack");
  await expect(panel).toContainText("現在の検索に使用している版 v1");
  await expect(panel).toContainText("別の版で構築中");
  await panel.getByRole("button", { name: "公開時の完全な設定", exact: true }).click();
  await expect(panel.getByLabel("公開時の完全な設定 v1")).toHaveAttribute("readonly");
  await page.route(`**/api/documents/${DOC_ID}`, route => route.fulfill(ok({ ...documentDetail("INDEXED"), file_name: "sample.txt", language_pack: { current_release_id: "release-2", current_version: 2, recipes: [{ ...used, target_release_id: null, target_version: null }] } })));
  await page.reload();
  await panel.getByRole("button", { name: "差分と影響を確認する" }).click();
  await panel.getByRole("combobox", { name: "再処理を始める工程" }).click();
  await page.getByRole("option", { name: "Chunk 作成", exact: true }).click();
  await panel.getByRole("button", { name: "再処理する", exact: true }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("言語資源 v2");
  await expect(dialog).toContainText("レシピ1");
  await dialog.getByRole("button", { name: "再処理する", exact: true }).click();
  await expect.poll(() => submitted?.searchParams.get("expected_language_pack_release_id")).toBe("release-2");
  expect(submitted!.searchParams.get("expected_config_revision")).toBe("1");
  await expect(panel.getByText("言語資源の現在版が変わりました。再確認してください。", { exact: true })).toBeVisible();
  await expect(panel).toContainText("現在の検索に使用している版 v1");
});

// #4523: 索引の有無・使用版の記録・構築の対象版で状態を分け、存在しない索引・版差分を示さない。
const noIndexUse = { recipe_id: "recipe-1", chunk_set_id: null, release_id: null, version: null, ingest_hash: null, relation: "unrecorded", built_at: null, job_id: null, target_release_id: null, target_version: null };
const servingUse = (release: string, version: number, relation: string) => ({ ...noIndexUse, chunk_set_id: "chunks-a", release_id: release, version, ingest_hash: `hash-${version}`, relation, built_at: "2026-10-17T00:00:00Z", job_id: "job-a" });

async function openLanguagePackState(page: Page, recipes: Record<string, unknown>[], building: boolean) {
  await mockWorkspace(page, "INDEXED", "unused");
  const recipe = { ...recipeView("INDEXED"), active_chunk_set_id: recipes.some(item => item.chunk_set_id) ? "chunks-a" : null, steps: recipeView("INDEXED").steps.map(step => ({ ...step, status: building ? "QUEUED" : "SUCCEEDED" })) };
  await page.route(`**/api/documents/${DOC_ID}/recipes`, route => route.fulfill(ok([recipe])));
  await page.route(`**/api/documents/${DOC_ID}`, route => route.fulfill(ok({ ...documentDetail("INDEXED"), file_name: "sample.txt", language_pack: { current_release_id: "release-2", current_version: 2, recipes } })));
  const comparisons: URL[] = [];
  await page.route("**/api/settings/language-pack/releases/compare?*", route => {
    const url = new URL(route.request().url());
    comparisons.push(url);
    return route.fulfill(ok({ before_id: url.searchParams.get("before_id"), after_id: url.searchParams.get("after_id"), differences: [{ section: "numbers", key: "article_units", before: ["A"], after: ["A", "B"], ingest: true }], ingest_changed: true, impact_requires_review: true, affected_stages: [] }));
  });
  await page.goto(`/documents/${DOC_ID}`);
  const panel = page.getByTestId("document-language-pack");
  await expect(panel).toBeVisible();
  return { panel, comparisons };
}

test("言語資源: 索引が無く構築中の job も無ければ未構築とし、比較の操作を出さない", { tag: "@responsive" }, async ({ page }) => {
  const { panel } = await openLanguagePackState(page, [], false);
  await expect(panel).toHaveAttribute("data-state", "notBuilt");
  await expect(panel).toContainText("まだ検索に使用できる索引はありません");
  await expect(panel).not.toContainText("版未記録");
  await expect(panel.getByRole("button")).toHaveCount(0);
  await expectNoPageOverflow(page);
});

test("言語資源: 初回構築は対象版だけを示し、別の版・既存の索引・差分を示さない", { tag: "@responsive" }, async ({ page }, testInfo) => {
  const { panel, comparisons } = await openLanguagePackState(page, [{ ...noIndexUse, target_release_id: "release-2", target_version: 2 }], true);
  await expect(panel).toHaveAttribute("data-state", "firstBuild");
  await expect(panel).toContainText("まだ検索に使用できる索引はありません");
  await expect(panel).toContainText("初回構築中");
  await expect(panel).toContainText("構築中の対象版 v2");
  for (const text of ["別の版で構築中", "既存の索引", "版未記録"]) await expect(panel).not.toContainText(text);
  await expect(panel.getByRole("button", { name: "差分と影響を確認する" })).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "構築中の対象版を確認する" })).toBeVisible();
  expect(comparisons).toHaveLength(0);
  await expectNoPageOverflow(page);
  await panel.screenshot({ path: testInfo.outputPath("language-pack-first-build.png") });
});

test("言語資源: 使用中と同じ版の構築は換版として扱わず、比較を出さない", { tag: "@responsive" }, async ({ page }) => {
  const { panel, comparisons } = await openLanguagePackState(page, [{ ...servingUse("release-2", 2, "current"), target_release_id: "release-2", target_version: 2 }], true);
  await expect(panel).toHaveAttribute("data-state", "sameVersionBuild");
  await expect(panel).toContainText("現在の検索に使用している版 v2");
  await expect(panel).toContainText("同じ版で構築中");
  await expect(panel).not.toContainText("別の版で構築中");
  await expect(panel.getByRole("button", { name: "差分と影響を確認する" })).toHaveCount(0);
  expect(comparisons).toHaveLength(0);
  await expectNoPageOverflow(page);
});

test("言語資源: 別の版の構築は使用中 → 構築の対象版で差分を比べる", { tag: "@responsive" }, async ({ page }, testInfo) => {
  // 現在版（v2）とも別の対象版（v3）を構築中にし、比較先が現在版でなく構築の対象版であることを確かめる。
  const { panel, comparisons } = await openLanguagePackState(page, [{ ...servingUse("release-1", 1, "different"), target_release_id: "release-3", target_version: 3 }], true);
  await expect(panel).toHaveAttribute("data-state", "differentVersionBuild");
  await expect(panel).toContainText("別の版で構築中");
  await expect(panel).toContainText("構築中の対象版 v3");
  await expect(panel).toContainText("成功するまで既存の索引を使います。");
  await panel.getByRole("button", { name: "差分と影響を確認する" }).click();
  await expect.poll(() => comparisons.map(url => `${url.searchParams.get("before_id")}>${url.searchParams.get("after_id")}`)).toEqual(["release-1>release-3"]);
  await expect(panel.getByText("取込設定に差分があります", { exact: true })).toBeVisible();
  // 構築中は再処理を受け付けないので、工程の選択を出さない。
  await expect(panel.getByRole("combobox", { name: "再処理を始める工程" })).toHaveCount(0);
  await expectNoPageOverflow(page);
  await panel.screenshot({ path: testInfo.outputPath("language-pack-different-build.png") });
});

test("言語資源: 使用版が未記録の索引は判定不可とし、別の版として扱わない", { tag: "@responsive" }, async ({ page }, testInfo) => {
  const unrecorded = { ...servingUse("release-1", 1, "different"), ingest_hash: null, relation: "unrecorded" };
  const { panel, comparisons } = await openLanguagePackState(page, [{ ...unrecorded, target_release_id: "release-2", target_version: 2 }], true);
  await expect(panel).toHaveAttribute("data-state", "unrecorded");
  await expect(panel).toContainText("現在の検索に使用している版 未記録・判定不可");
  await expect(panel).toContainText("構築中（使用版との関係は判定不可）");
  await expect(panel).not.toContainText("別の版で構築中");
  await expect(panel.getByRole("button", { name: "差分と影響を確認する" })).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "再処理を確認する" })).toHaveCount(0);
  await expectNoPageOverflow(page);
  await panel.screenshot({ path: testInfo.outputPath("language-pack-unrecorded-build.png") });

  // 構築中でなければ、差分を出さずに再処理の判断だけを残す（従来の未記録の再処理）。
  await page.route(`**/api/documents/${DOC_ID}`, route => route.fulfill(ok({ ...documentDetail("INDEXED"), file_name: "sample.txt", language_pack: { current_release_id: "release-2", current_version: 2, recipes: [unrecorded] } })));
  await page.route(`**/api/documents/${DOC_ID}/recipes`, route => route.fulfill(ok([{ ...recipeView("INDEXED"), active_chunk_set_id: "chunks-a", steps: recipeView("INDEXED").steps.map(step => ({ ...step, status: "SUCCEEDED" })) }])));
  await page.reload();
  await expect(panel).toHaveAttribute("data-state", "unrecorded");
  await panel.getByRole("button", { name: "再処理を確認する" }).click();
  await expect(panel.getByText("使用版の情報がないため、現在版との関係を判定できません。文書内容の誤りを示すものではありません。")).toBeVisible();
  await expect(panel.getByRole("combobox", { name: "再処理を始める工程" })).toBeVisible();
  expect(comparisons).toHaveLength(0);
});
