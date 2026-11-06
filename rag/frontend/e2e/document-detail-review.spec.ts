import { expect, type Page, test } from "./fixtures/test";
import { expectNoPageOverflow, mockAuthUser, mockDatabaseReady, mockLocalAuth } from "./_helpers";
import { t } from "../src/lib/i18n";

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


// #4566: 言語資源は処理レシピの区画の要約の行（「言語資源 v{n} [状態]」）1 つにまとめ、差分・公開時の設定・再処理は
// 言語資源の設定の権限を持つ利用者の「詳細」にだけ置く。構築中の案内は状態メッセージの枠の 1 本だけ。
const noIndexUse = { recipe_id: "recipe-1", chunk_set_id: null, release_id: null, version: null, ingest_hash: null, relation: "unrecorded", built_at: null, job_id: null, target_release_id: null, target_version: null };
const servingUse = (release: string, version: number, relation: string) => ({ ...noIndexUse, chunk_set_id: "chunks-a", release_id: release, version, ingest_hash: `hash-${version}`, relation, built_at: "2026-10-17T00:00:00Z", job_id: "job-a" });
const unrecordedUse = { ...servingUse("release-1", 1, "different"), ingest_hash: null, relation: "unrecorded" };
const releaseSnapshot = (id: string, version: number) => ({ id, version, effective: { numbers: { article_units: ["A"] } }, hash: "old", ingest_hash: "old", published_at: "2026-10-17T00:00:00Z", published_by: null, previous_id: null, restored_from: null, change_note: "" });

type LanguagePackStateOptions = { building?: boolean; admin?: boolean; theme?: "light" | "dark" };

async function openLanguagePackState(page: Page, recipes: Record<string, unknown>[], { building = false, admin = true, theme }: LanguagePackStateOptions = {}) {
  if (theme) {
    await page.addInitScript(value => {
      localStorage.setItem("production-ready-rag.ui", JSON.stringify({ state: { theme: value }, version: 0 }));
    }, theme);
  }
  await mockWorkspace(page, "INDEXED", "unused");
  // 言語資源の設定の権限を持たない利用者（文書インデックスとアップロードだけ）。
  if (!admin) await mockAuthUser(page, { permissions: ["menu.file_list", "menu.upload"] });
  const recipe = { ...recipeView("INDEXED"), active_chunk_set_id: recipes.some(item => item.chunk_set_id) ? "chunks-a" : null, searchable: recipes.some(item => item.chunk_set_id), steps: recipeView("INDEXED").steps.map(step => ({ ...step, status: building ? "QUEUED" : "SUCCEEDED" })) };
  await page.route(`**/api/documents/${DOC_ID}/recipes`, route => route.fulfill(ok([recipe])));
  await page.route(`**/api/documents/${DOC_ID}`, route => route.fulfill(ok({ ...documentDetail("INDEXED"), file_name: "sample.txt", language_pack: { current_release_id: "release-2", current_version: 2, recipes } })));
  const comparisons: URL[] = [];
  const snapshots: string[] = [];
  await page.route("**/api/settings/language-pack/releases/compare?*", route => {
    const url = new URL(route.request().url());
    comparisons.push(url);
    return route.fulfill(ok({ before_id: url.searchParams.get("before_id"), after_id: url.searchParams.get("after_id"), differences: [{ section: "numbers", key: "article_units", before: ["A"], after: ["A", "B"], ingest: true }], ingest_changed: true, impact_requires_review: true, affected_stages: [] }));
  });
  await page.route("**/api/settings/language-pack/releases/release-*", route => {
    const id = new URL(route.request().url()).pathname.split("/").at(-1)!;
    snapshots.push(id);
    return route.fulfill(ok(releaseSnapshot(id, Number(id.replace("release-", "")))));
  });
  await page.goto(`/documents/${DOC_ID}`);
  const panel = page.getByTestId("document-language-pack");
  await expect(panel).toBeVisible();
  // 別のカードを置かず、処理レシピの区画（選んだレシピ）の中に 1 行だけ置く。
  await expect(page.locator('section[aria-label="処理レシピ"]').getByTestId("document-language-pack")).toHaveCount(1);
  await expect(page.getByText("言語資源の使用版", { exact: true })).toHaveCount(0);
  return { panel, details: panel.getByTestId("document-language-pack-details"), comparisons, snapshots };
}

async function expectBadge(panel: ReturnType<Page["getByTestId"]>, label: string, variant: string) {
  const badge = panel.locator("[data-status-variant]");
  await expect(badge).toHaveCount(1);
  await expect(badge).toHaveText(label);
  await expect(badge).toHaveAttribute("data-status-variant", variant);
  // 状態は色だけで示さない（StatusBadge のアイコン）。
  await expect(badge.locator("svg")).toHaveCount(1);
}

test("言語資源: 差分があれば「再処理を推奨」で詳細を自動で開き、固定した公開版で再処理を確認する", { tag: "@responsive" }, async ({ page }) => {
  const { panel, details, comparisons, snapshots } = await openLanguagePackState(page, [servingUse("release-1", 1, "different")]);
  await expect(panel).toHaveAttribute("data-status", "different");
  await expect(panel).toContainText("言語資源 v1");
  await expectBadge(panel, "再処理を推奨", "warning");
  // 対応が要るので「詳細」を開いて出す。管理者には依頼の文を出さない。
  await expect(details).toHaveAttribute("data-state", "open");
  await expect(panel.getByTestId("document-language-pack-ask-admin")).toHaveCount(0);
  await expect(details).toContainText("取込設定に差分あり");
  await expect(details).toContainText("job-a");
  await expect(details).toContainText("v2");
  // 公開時の完全な設定は開いたときだけ読む。
  expect(snapshots).toHaveLength(0);
  await details.getByTestId("document-language-pack-snapshot-toggle").click();
  await expect(details.getByRole("textbox", { name: "公開時の完全な設定 v1" })).toHaveAttribute("readonly");
  expect(snapshots).toEqual(["release-1"]);

  let submitted: URL | null = null;
  await page.route(`**/api/documents/${DOC_ID}/recipes/recipe-1/ingestion-jobs?*`, async route => {
    submitted = new URL(route.request().url());
    await route.fulfill({ status: 409, json: { data: null, error_messages: ["言語資源の現在版が変わりました。再確認してください。"], warning_messages: [] } });
  });
  await details.getByRole("button", { name: "差分と影響を確認する" }).click();
  await expect.poll(() => comparisons.map(url => `${url.searchParams.get("before_id")}>${url.searchParams.get("after_id")}`)).toEqual(["release-1>release-2"]);
  await expect(details.getByText("取込設定に差分があります", { exact: true })).toBeVisible();
  await details.getByRole("combobox", { name: "再処理を始める工程" }).click();
  await page.getByRole("option", { name: "Chunk 作成", exact: true }).click();
  await details.getByRole("button", { name: "再処理する", exact: true }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("言語資源 v2");
  await expect(dialog).toContainText("レシピ1");
  await dialog.getByRole("button", { name: "再処理する", exact: true }).click();
  await expect.poll(() => submitted?.searchParams.get("expected_language_pack_release_id")).toBe("release-2");
  expect(submitted!.searchParams.get("phase")).toBe("CHUNK");
  expect(submitted!.searchParams.get("expected_config_revision")).toBe("1");
  await expect(details.getByText("言語資源の現在版が変わりました。再確認してください。", { exact: true })).toBeVisible();
  await expectNoPageOverflow(page);
});

test("言語資源: 権限の無い利用者には要約の行だけを出し、対応が要るときは管理者への依頼を示す", { tag: "@responsive" }, async ({ page }) => {
  const { panel, details, comparisons, snapshots } = await openLanguagePackState(page, [servingUse("release-1", 1, "different")], { admin: false });
  await expectBadge(panel, "再処理を推奨", "warning");
  await expect(panel.getByTestId("document-language-pack-ask-admin")).toHaveText("管理者に再処理を依頼してください");
  // 使えない操作・導線を描かない（messaging §3.4.1）。
  await expect(details).toHaveCount(0);
  await expect(panel.locator("a, button, select, textarea, input, summary")).toHaveCount(0);
  expect(comparisons).toHaveLength(0);
  expect(snapshots).toHaveLength(0);
  await expectNoPageOverflow(page);
});

test("言語資源: 権限の無い利用者は、対応の要らない状態では依頼の文を出さない", { tag: "@responsive" }, async ({ page }) => {
  const { panel } = await openLanguagePackState(page, [servingUse("release-2", 2, "current")], { admin: false });
  await expectBadge(panel, "最新", "neutral");
  await expect(panel.getByTestId("document-language-pack-ask-admin")).toHaveCount(0);
  await expect(panel.locator("a, button, summary")).toHaveCount(0);
});

test("言語資源: 権限の無い利用者にも、判定できない索引では依頼の文を出す", { tag: "@responsive" }, async ({ page }) => {
  const { panel } = await openLanguagePackState(page, [unrecordedUse], { admin: false });
  await expectBadge(panel, "判定できません", "warning");
  await expect(panel.getByTestId("document-language-pack-ask-admin")).toBeVisible();
  await expect(panel.locator("a, button, summary")).toHaveCount(0);
});

// #4194: 使用中の版が現在の契約で読めない（公開時の設定・比較が 409）とき、行き止まりにせず抽出から再処理できる。
for (const theme of ["light", "dark"] as const) {
  test(`言語資源: 使用中の版を現在の契約で読めないときは比較できないことを示し、ファイル準備か抽出から再処理できる（${theme}）`, { tag: "@responsive" }, async ({ page }, testInfo) => {
    await page.addInitScript(value => {
      localStorage.setItem("production-ready-rag.ui", JSON.stringify({ state: { theme: value }, version: 0 }));
    }, theme);
    await mockWorkspace(page, "INDEXED", "合成の投入エラー");
    const recipe = { ...recipeView("INDEXED"), active_chunk_set_id: "chunks-old", searchable: true };
    const used = { ...servingUse("release-1", 1, "different"), chunk_set_id: "chunks-old" };
    const unreadable = { status: 409, json: { data: null, error_messages: ["保存済み言語資源が現在の契約を満たしません。データの準備が必要です。"], warning_messages: [] } };
    await page.route(`**/api/documents/${DOC_ID}/recipes`, route => route.fulfill(ok([recipe])));
    await page.route(`**/api/documents/${DOC_ID}`, route => route.fulfill(ok({ ...documentDetail("INDEXED"), file_name: "sample.txt", language_pack: { current_release_id: "release-2", current_version: 2, recipes: [used] } })));
    await page.route("**/api/settings/language-pack/releases/release-1", route => route.fulfill(unreadable));
    await page.route("**/api/settings/language-pack/releases/compare?*", route => route.fulfill(unreadable));
    const submitted: URL[] = [];
    await page.route(`**/api/documents/${DOC_ID}/recipes/recipe-1/ingestion-jobs?*`, async route => {
      submitted.push(new URL(route.request().url()));
      await route.fulfill(ok({ ...failedJob(), id: "job-new", status: "QUEUED", phase: "EXTRACT", error_message: null, attempt_count: 0, started_at: null, finished_at: null }));
    });
    await page.goto(`/documents/${DOC_ID}`);
    const details = page.getByTestId("document-language-pack-details");
    await expect(details).toHaveAttribute("data-state", "open");
    await details.getByRole("button", { name: "差分と影響を確認する" }).click();
    await expect(details.getByText(t("settings.language.release.unreadableTitle"), { exact: true })).toBeVisible();
    await expect(details.getByText(t("settings.language.release.unreadableHelp"), { exact: true })).toBeVisible();
    // 汎用のエラー表示（再試行）にしない。
    await expect(details.getByRole("button", { name: "再試行" })).toHaveCount(0);
    await details.getByTestId("document-language-pack-snapshot-toggle").click();
    await expect(details.getByText(t("settings.language.release.unreadableSnapshot"), { exact: true })).toBeVisible();
    await page.getByTestId("document-language-pack").screenshot({ path: testInfo.outputPath(`unreadable-panel-review-${theme}.png`) });
    await details.getByRole("combobox", { name: "再処理を始める工程" }).click();
    await expect(page.getByRole("option", { name: "Chunk 作成", exact: true })).toHaveCount(0);
    await expect(page.getByRole("option", { name: "Embedding / 索引", exact: true })).toHaveCount(0);
    await expect(page.getByRole("option", { name: "ファイル準備", exact: true })).toBeVisible();
    await page.getByRole("option", { name: "抽出", exact: true }).click();
    await details.getByRole("button", { name: "再処理する", exact: true }).click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText("開始工程: 抽出");
    await dialog.getByRole("button", { name: "再処理する", exact: true }).click();
    await expect.poll(() => submitted.map(url => url.searchParams.get("phase"))).toEqual(["EXTRACT"]);
    expect(submitted[0].searchParams.get("expected_language_pack_release_id")).toBe("release-2");
    await expect(details.getByText(t("settings.language.release.queued"), { exact: true })).toBeVisible();
    await expectNoPageOverflow(page);
  });
}

test("言語資源: 索引も構築中の job も無ければ「未構築」とし、版・詳細・操作を出さない", { tag: "@responsive" }, async ({ page }) => {
  const { panel, details } = await openLanguagePackState(page, []);
  await expect(panel).toHaveAttribute("data-state", "notBuilt");
  await expectBadge(panel, "未構築", "neutral");
  await expect(panel).not.toContainText("言語資源 v");
  await expect(details).toHaveCount(0);
  await expect(panel.getByRole("button")).toHaveCount(0);
  await expectNoPageOverflow(page);
});

test("言語資源: 構築中は「構築中」とし、対象版の案内は状態メッセージの枠の 1 本だけに出す", { tag: "@responsive" }, async ({ page }) => {
  // 初回の構築: 既存の索引・差分を示さない。
  const { panel, details, comparisons } = await openLanguagePackState(page, [{ ...noIndexUse, target_release_id: "release-2", target_version: 2 }], { building: true });
  await expect(panel).toHaveAttribute("data-state", "firstBuild");
  await expect(panel).toContainText("言語資源 v2");
  await expectBadge(panel, "構築中", "info");
  const slot = page.getByTestId("document-language-pack-building");
  await expect(slot).toHaveText("言語資源 v2 で構築しています。成功すると、この版の索引を検索に使います。");
  await expect(page.getByTestId("document-status-actions")).toContainText("言語資源 v2 で構築しています。");
  // 構築の案内は 1 か所だけ（要約の行・詳細に Banner を重ねない）。
  await expect(page.getByText(/で構築しています。/)).toHaveCount(1);
  for (const text of ["初回構築中", "同じ版で構築中", "別の版で構築中"]) await expect(page.getByText(text)).toHaveCount(0);
  // 対応の要らない状態は「詳細」を閉じて出す。
  await expect(details).toHaveAttribute("data-state", "closed");
  await panel.getByTestId("document-language-pack-details-toggle").click();
  await expect(details).toContainText("まだ検索に使用できる索引はありません");
  await expect(details.getByTestId("document-language-pack-target-snapshot")).toBeVisible();
  await expect(details.getByRole("button", { name: "差分と影響を確認する" })).toHaveCount(0);
  await expect(details.getByRole("alert")).toHaveCount(0);
  expect(comparisons).toHaveLength(0);
  await expectNoPageOverflow(page);
});

test("言語資源: 使用中と同じ版の構築は比較を出さず、既存の索引を使い続けることを状態メッセージで示す", { tag: "@responsive" }, async ({ page }) => {
  const { panel, details, comparisons } = await openLanguagePackState(page, [{ ...servingUse("release-2", 2, "current"), target_release_id: "release-2", target_version: 2 }], { building: true });
  await expect(panel).toHaveAttribute("data-state", "sameVersionBuild");
  await expectBadge(panel, "構築中", "info");
  await expect(page.getByTestId("document-language-pack-building")).toHaveText("言語資源 v2 で構築しています。成功するまで既存の索引を検索に使います。");
  await panel.getByTestId("document-language-pack-details-toggle").click();
  await expect(details.getByRole("button", { name: "差分と影響を確認する" })).toHaveCount(0);
  // 同じ版なので、構築中の対象版の公開時の設定を別に出さない。
  await expect(details.getByTestId("document-language-pack-target-snapshot")).toHaveCount(0);
  expect(comparisons).toHaveLength(0);
});

test("言語資源: 別の版の構築は使用中 → 構築の対象版で差分を比べ、再処理は受け付けない", { tag: "@responsive" }, async ({ page }) => {
  // 現在版（v2）とも別の対象版（v3）を構築中にし、比較先が現在版でなく構築の対象版であることを確かめる。
  const { panel, details, comparisons } = await openLanguagePackState(page, [{ ...servingUse("release-1", 1, "different"), target_release_id: "release-3", target_version: 3 }], { building: true });
  await expect(panel).toHaveAttribute("data-state", "differentVersionBuild");
  await expect(panel).toContainText("言語資源 v3");
  await expectBadge(panel, "構築中", "info");
  await expect(page.getByTestId("document-language-pack-building")).toHaveText("言語資源 v3 で構築しています。成功するまで既存の索引を検索に使います。");
  await panel.getByTestId("document-language-pack-details-toggle").click();
  await details.getByRole("button", { name: "差分と影響を確認する" }).click();
  await expect.poll(() => comparisons.map(url => `${url.searchParams.get("before_id")}>${url.searchParams.get("after_id")}`)).toEqual(["release-1>release-3"]);
  await expect(details.getByText("取込設定に差分があります", { exact: true })).toBeVisible();
  // 構築中は再処理を受け付けないので、工程の選択を出さない。
  await expect(details.getByRole("combobox", { name: "再処理を始める工程" })).toHaveCount(0);
  await expectNoPageOverflow(page);
});

test("言語資源: 使用版が未記録の索引は「判定できません」とし、別の版として扱わない", { tag: "@responsive" }, async ({ page }) => {
  // 構築中なら「構築中」。判定できない索引との比較・再処理を出さない。
  const { panel, details, comparisons } = await openLanguagePackState(page, [{ ...unrecordedUse, target_release_id: "release-2", target_version: 2 }], { building: true });
  await expect(panel).toHaveAttribute("data-state", "unrecorded");
  await expectBadge(panel, "構築中", "info");
  await panel.getByTestId("document-language-pack-details-toggle").click();
  await expect(details).toContainText("未記録・判定不可");
  await expect(details.getByRole("button", { name: "差分と影響を確認する" })).toHaveCount(0);
  await expect(details.getByRole("button", { name: "再処理を確認する" })).toHaveCount(0);

  // 構築中でなければ「判定できません」で詳細を自動で開き、差分を出さずに再処理の判断だけを残す。
  await page.route(`**/api/documents/${DOC_ID}`, route => route.fulfill(ok({ ...documentDetail("INDEXED"), file_name: "sample.txt", language_pack: { current_release_id: "release-2", current_version: 2, recipes: [unrecordedUse] } })));
  await page.route(`**/api/documents/${DOC_ID}/recipes`, route => route.fulfill(ok([{ ...recipeView("INDEXED"), active_chunk_set_id: "chunks-a", steps: recipeView("INDEXED").steps.map(step => ({ ...step, status: "SUCCEEDED" })) }])));
  await page.reload();
  await expect(panel).toHaveAttribute("data-status", "unrecorded");
  await expectBadge(panel, "判定できません", "warning");
  await expect(panel).not.toContainText("言語資源 v");
  await expect(details).toHaveAttribute("data-state", "open");
  await details.getByRole("button", { name: "再処理を確認する" }).click();
  await expect(details.getByText("使用版の情報がないため、現在版との関係を判定できません。文書内容の誤りを示すものではありません。")).toBeVisible();
  await expect(details.getByRole("combobox", { name: "再処理を始める工程" })).toBeVisible();
  expect(comparisons).toHaveLength(0);
  await expectNoPageOverflow(page);
});

test("言語資源: 現在版の索引は「最新」で詳細を閉じ、比較・再処理を出さない", { tag: "@responsive" }, async ({ page }) => {
  const { panel, details, comparisons } = await openLanguagePackState(page, [servingUse("release-2", 2, "current")]);
  await expect(panel).toContainText("言語資源 v2");
  await expectBadge(panel, "最新", "neutral");
  await expect(details).toHaveAttribute("data-state", "closed");
  await panel.getByTestId("document-language-pack-details-toggle").click();
  await expect(details).toContainText("現在版を使用");
  await expect(details.getByRole("button")).toHaveCount(0);
  // 状態メッセージの枠に構築の案内を出さない。
  await expect(page.getByTestId("document-language-pack-building")).toHaveCount(0);
  expect(comparisons).toHaveLength(0);
});

test("言語資源: 取込設定が同じ版は「最新（内容は同じ）」とし、差分の確認はできるが再処理は出さない", { tag: "@responsive" }, async ({ page }) => {
  const { panel, details, comparisons } = await openLanguagePackState(page, [servingUse("release-1", 1, "equivalent")]);
  await expectBadge(panel, "最新（内容は同じ）", "neutral");
  await expect(details).toHaveAttribute("data-state", "closed");
  await panel.getByTestId("document-language-pack-details-toggle").click();
  await details.getByRole("button", { name: "差分と影響を確認する" }).click();
  await expect.poll(() => comparisons.length).toBe(1);
  await expect(details.getByRole("combobox", { name: "再処理を始める工程" })).toHaveCount(0);
});

for (const theme of ["light", "dark"] as const) {
  test(`言語資源: 要約の行と詳細はライト・ダークで読め、狭い画面で横にあふれない（${theme}）`, { tag: "@responsive" }, async ({ page }, testInfo) => {
    const { panel, details } = await openLanguagePackState(page, [{ ...servingUse("release-1", 1, "different"), job_id: "job-0123456789abcdef0123456789abcdef" }], { theme });
    await expect(details).toHaveAttribute("data-state", "open");
    await details.getByRole("button", { name: "差分と影響を確認する" }).click();
    await expect(details.getByRole("combobox", { name: "再処理を始める工程" })).toBeVisible();
    await expectNoPageOverflow(page);
    await panel.screenshot({ path: testInfo.outputPath(`language-pack-summary-${theme}.png`) });
  });
}
