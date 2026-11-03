import { fulfillDocumentInspection } from "./document-inspection-mocks";
import { expect, type Page, type Route, test } from "./fixtures/test";
import { mockDatabaseReady, mockLocalAuth } from "./_helpers";

// 抽出の修正の履歴（#2644）。版の一覧・差分・旧版の復元と、再解析の案内を確かめる。

const DOC_ID = "doc-extraction-revisions";
const CURRENT_ID = "er-current";

const ok = (data: unknown) => ({ json: { data, error_messages: [], warning_messages: [] } });

function documentDetail(status: string) {
  return {
    id: DOC_ID,
    file_name: "synthetic.txt",
    status,
    category_name: null,
    content_type: "text/plain",
    file_size_bytes: 64,
    content_sha256: "a".repeat(64),
    duplicate_of_document_id: null,
    uploaded_at: "2026-06-18T00:00:00Z",
    indexed_at: null,
    object_storage_path: "local://synthetic.txt",
    error_message: null,
    extraction: {
      raw_text: "見出し A\n本文 B",
      document_type: "文書",
      confidence: 0.98,
      warnings: [],
      pages: [{ page_number: 1, width: 612, height: 792, element_ids: ["el-0"] }],
      elements: [
        {
          kind: "text",
          text: "本文 B",
          order: 0,
          element_id: "el-0",
          content_kind: "text",
          source_parser: "local_text_structure",
          page_number: 1,
          bbox: [0, 0, 100, 40],
          section_path: [],
          confidence: 0.98,
          metadata: {},
        },
      ],
      tables: [],
    },
    knowledge_bases: [],
    source_profile: null,
  };
}

function recipeView(status: string, active = CURRENT_ID) {
  return {
    recipe_id: "recipe-1",
    document_id: DOC_ID,
    slot_no: 1 as const,
    status,
    failed_phase: null,
    processing_config: {},
    effective_processing_config: {},
    preprocess_artifact: null,
    active_extraction_recipe_id: active,
    active_chunk_set_id: null,
    chunk_count: 0,
    vector_count: 0,
    config_revision: 1,
    materialized_revision: null,
    searchable: false,
    needs_reprocessing: false,
    error_message: null,
    steps: [
      { phase: "PREPROCESS", status: "SUCCEEDED", started_at: null, finished_at: null, error_message: null },
      { phase: "EXTRACT", status: "SUCCEEDED", started_at: null, finished_at: null, error_message: null },
      { phase: "CHUNK", status: "PENDING", started_at: null, finished_at: null, error_message: null },
      { phase: "INDEX", status: "PENDING", started_at: null, finished_at: null, error_message: null },
    ],
    created_at: "2026-06-18T00:00:00Z",
    updated_at: "2026-06-18T00:00:05Z",
    started_at: null,
    finished_at: null,
  };
}

function revision(overrides: Record<string, unknown>) {
  return {
    parent_revision_no: null,
    base_revision_no: 1,
    restored_from_revision_no: null,
    kind: "edited",
    changed_element_count: 1,
    created_at: "2026-06-18T00:00:00Z",
    is_current: false,
    is_serving: false,
    base_changed: false,
    settings_changed: false,
    ...overrides,
  };
}

function history(overrides: Record<string, unknown> = {}) {
  return {
    revisions: [
      revision({ revision_no: 3, parent_revision_no: 2, is_current: true }),
      revision({ revision_no: 2, parent_revision_no: 1, is_serving: true }),
      revision({ revision_no: 1, kind: "parsed", changed_element_count: 0 }),
    ],
    current_revision_no: 3,
    reparsed_since_last_revision: false,
    max_revisions: 20,
    ...overrides,
  };
}

interface MockState {
  history: ReturnType<typeof history>;
  status: string;
  restoreBodies: unknown[];
  restoreFailure?: string;
  diffUrls: URL[];
}

async function mockWorkspace(page: Page, state: MockState) {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
  await page.route("**/api/knowledge-bases**", (route) =>
    route.fulfill(ok({ items: [], total: 0, limit: 100, offset: 0, has_next: false })),
  );
  await page.route(`**/api/documents/${DOC_ID}**`, async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith("/extraction-revisions")) return route.fulfill(ok(state.history));
    if (path.endsWith("/restore")) {
      state.restoreBodies.push(route.request().postDataJSON());
      if (state.restoreFailure) {
        return route.fulfill({
          status: 409,
          json: { data: null, error_messages: [state.restoreFailure], warning_messages: [] },
        });
      }
      state.history = history({
        revisions: [
          revision({ revision_no: 4, parent_revision_no: 3, kind: "restored", restored_from_revision_no: 1, is_current: true }),
          ...state.history.revisions.map((row) => ({ ...row, is_current: false })),
        ],
        current_revision_no: 4,
      });
      return route.fulfill(ok(recipeView(state.status, "er-restored")));
    }
    if (path.endsWith("/diff")) {
      state.diffUrls.push(url);
      return route.fulfill(
        ok({
          revision_no: 3,
          against_revision_no: url.searchParams.get("against") ? Number(url.searchParams.get("against")) : 2,
          total: 1,
          offset: 0,
          limit: 25,
          changes: [
            {
              element_id: "el-0",
              change: "changed",
              page_number: 1,
              before: "修正の前の本文",
              after: "修正の後の本文",
              truncated: false,
            },
          ],
        }),
      );
    }
    if (path.endsWith("/recipes")) return route.fulfill(ok([recipeView(state.status)]));
    if (path.endsWith("/ingestion-jobs") || path.endsWith("/ingestion-segments")) return route.fulfill(ok([]));
    if (path.endsWith("/chunk-sets") || path.endsWith("/chunks") || path.endsWith("/knowledge-bases")) {
      return route.fulfill(ok([]));
    }
    if (path.endsWith(`/documents/${DOC_ID}`)) return route.fulfill(ok(documentDetail(state.status)));
    if (path.endsWith("/extraction-view")) {
      return fulfillDocumentInspection(route, ok(documentDetail(state.status).extraction));
    }
    return route.fulfill({ status: 404, json: { data: null, error_messages: ["not found"], warning_messages: [] } });
  });
}

function newState(overrides: Partial<MockState> = {}): MockState {
  return { history: history(), status: "REVIEW", restoreBodies: [], diffUrls: [], ...overrides };
}

async function openHistory(page: Page) {
  await page.goto(`/documents/${DOC_ID}`);
  await page.getByRole("tab", { name: "構造化要素" }).click();
  await page.getByRole("button", { name: /^修正の履歴/ }).click();
  const sheet = page.getByRole("dialog", { name: "抽出の修正の履歴" });
  await expect(sheet).toBeVisible();
  return sheet;
}

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => {
    const dialog = document.querySelector('[role="dialog"]');
    if (!dialog) return 0;
    return dialog.scrollWidth - dialog.clientWidth;
  });
  expect(overflow).toBeLessThanOrEqual(1);
}

test.describe("抽出の修正の履歴", { tag: "@responsive" }, () => {
  test("版の種類と今の版・検索に使用中を区別して一覧できる", async ({ page }) => {
    await mockWorkspace(page, newState());

    const sheet = await openHistory(page);

    const rows = sheet.getByRole("row");
    await expect(rows.filter({ hasText: "版 3" })).toContainText("今の版");
    await expect(rows.filter({ hasText: "版 2" })).toContainText("検索に使用中");
    await expect(rows.filter({ hasText: "版 1" })).toContainText("解析の結果");
    await expect(sheet).toContainText("直近 20 版を残します");
    await expectNoHorizontalOverflow(page);
  });

  test("差分は既定で親の版と比べ、比べる版を選べる", async ({ page }) => {
    const state = newState();
    await mockWorkspace(page, state);

    const sheet = await openHistory(page);
    await sheet.getByTestId("extraction-revision-actions-3").getByRole("button").click();
    await page.getByRole("menuitem", { name: "差分を見る" }).click();

    const diff = sheet.getByTestId("extraction-revision-diff");
    await expect(diff).toContainText("修正の前の本文");
    await expect(diff).toContainText("修正の後の本文");
    expect(state.diffUrls[0]?.searchParams.get("against")).toBeNull();

    await diff.getByRole("combobox", { name: "比べる版" }).click();
    await page.getByRole("option", { name: /^版 1/ }).click();
    await expect.poll(() => state.diffUrls.at(-1)?.searchParams.get("against")).toBe("1");
    await expectNoHorizontalOverflow(page);
  });

  test("旧版への復元は確認の後に新しい版になる", async ({ page }) => {
    const state = newState();
    await mockWorkspace(page, state);

    const sheet = await openHistory(page);
    await sheet.getByTestId("extraction-revision-actions-1").getByRole("button").click();
    await page.getByRole("menuitem", { name: "この版に戻す" }).click();
    const confirmDialog = page.getByRole("alertdialog");
    await expect(confirmDialog).toContainText("今の版は履歴に残ります");
    await confirmDialog.getByRole("button", { name: "この版に戻す" }).click();

    await expect.poll(() => state.restoreBodies).toEqual([{ expected_extraction_recipe_id: CURRENT_ID }]);
    await expect(sheet.getByRole("row").filter({ hasText: "版 4" })).toContainText("版 1 から復元");
  });

  test("ほかの保存が先に入った復元の失敗を表示する", async ({ page }) => {
    await mockWorkspace(
      page,
      newState({ restoreFailure: "読み込んだ後に、ほかの操作で抽出結果が変わりました。開き直してください。" }),
    );

    const sheet = await openHistory(page);
    await sheet.getByTestId("extraction-revision-actions-1").getByRole("button").click();
    await page.getByRole("menuitem", { name: "この版に戻す" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "この版に戻す" }).click();

    await expect(sheet).toContainText("ほかの操作で抽出結果が変わりました");
  });

  test("再解析の後は前提が変わったことを示し、設定の違う版は戻せない", async ({ page }) => {
    await mockWorkspace(
      page,
      newState({
        history: history({
          revisions: [
            revision({ revision_no: 2, parent_revision_no: 1, base_changed: true }),
            revision({ revision_no: 1, kind: "parsed", base_changed: true, settings_changed: true }),
          ],
          current_revision_no: null,
          reparsed_since_last_revision: true,
        }),
      }),
    );

    await page.goto(`/documents/${DOC_ID}`);
    await page.getByRole("tab", { name: "構造化要素" }).click();
    // 閉じたシートも DOM に残る（SideSheet）ので、タブの中の案内を見る。
    await expect(
      page.locator("#inspector-panel-extraction").getByText("解析をやり直したため、修正の前提が変わりました"),
    ).toBeVisible();

    await page.getByRole("button", { name: /^修正の履歴/ }).click();
    const sheet = page.getByRole("dialog", { name: "抽出の修正の履歴" });
    const rows = sheet.getByRole("row");
    await expect(rows.filter({ hasText: "版 2" })).toContainText("今とは別の解析の結果を基にしています");
    await expect(rows.filter({ hasText: "版 1" })).toContainText("原本か解析の設定が今と違うため、戻せません");
    await sheet.getByTestId("extraction-revision-actions-1").getByRole("button").click();
    await expect(page.getByRole("menuitem", { name: "差分を見る" })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "この版に戻す" })).toHaveCount(0);
  });

  test("確認待ちでないレシピは履歴を読めるが戻せない", async ({ page }) => {
    await mockWorkspace(page, newState({ status: "INDEXED" }));

    const sheet = await openHistory(page);

    await expect(sheet).toContainText("旧版に戻せるのは確認待ちのレシピだけです");
    await sheet.getByTestId("extraction-revision-actions-1").getByRole("button").click();
    await expect(page.getByRole("menuitem", { name: "この版に戻す" })).toHaveCount(0);
  });
});
