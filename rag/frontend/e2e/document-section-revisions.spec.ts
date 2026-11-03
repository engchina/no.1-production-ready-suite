import { fulfillDocumentInspection } from "./document-inspection-mocks";
import { expect, type Page, type Route, test } from "./fixtures/test";
import { expectNoPageOverflow, mockDatabaseReady, mockLocalAuth } from "./_helpers";

// 人が修正した章節の履歴（#4171）。版の一覧・差分・旧版の復元と、基の抽出が変わった案内を確かめる。

const ok = (data: unknown) => ({
  json: { data, error_messages: [], warning_messages: [] },
});

const DOC_ID = "doc-section-revisions";

function documentDetail() {
  return {
    id: DOC_ID,
    file_name: "synthetic.txt",
    status: "INDEXED",
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
      raw_text: "本文 B",
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

function recipeView() {
  return {
    recipe_id: "recipe-1",
    document_id: DOC_ID,
    slot_no: 1 as const,
    status: "INDEXED",
    failed_phase: null,
    processing_config: {},
    effective_processing_config: {},
    preprocess_artifact: null,
    active_extraction_recipe_id: "er-current",
    active_chunk_set_id: null,
    chunk_count: 0,
    vector_count: 0,
    config_revision: 1,
    materialized_revision: null,
    searchable: false,
    needs_reprocessing: false,
    error_message: null,
    steps: [],
    created_at: "2026-06-18T00:00:00Z",
    updated_at: "2026-06-18T00:00:05Z",
    started_at: null,
    finished_at: null,
  };
}

function section(id: string, title: string, start: number, end: number) {
  return {
    id,
    title,
    level: 1,
    page_start: start,
    page_end: end,
    origin: "extraction",
    source_section_id: id,
    edited: false,
  };
}

function sectionsData(overrides: Record<string, unknown> = {}) {
  return {
    document_id: DOC_ID,
    source: "manual",
    sections: [section("nav-1", "合成の章 A", 1, 1), section("nav-2", "合成の章 B", 2, 2)],
    extraction_section_count: 2,
    page_count: 2,
    revision: 3,
    updated_at: "2026-06-18T00:00:00Z",
    base_changed: false,
    base_outline_changed: false,
    ...overrides,
  };
}

function revision(overrides: Record<string, unknown>) {
  return {
    parent_revision_no: null,
    restored_from_revision_no: null,
    kind: "edited",
    base_extraction_recipe_id: "er-a",
    changed_section_count: 1,
    created_at: "2026-06-18T00:00:00Z",
    is_current: false,
    base_changed: false,
    base_outline_changed: false,
    ...overrides,
  };
}

function history(overrides: Record<string, unknown> = {}) {
  return {
    revisions: [
      revision({ revision_no: 3, parent_revision_no: 2, is_current: true }),
      revision({ revision_no: 2, parent_revision_no: 1 }),
      revision({ revision_no: 1, kind: "extracted", changed_section_count: 0 }),
    ],
    current_revision_no: 3,
    max_revisions: 20,
    ...overrides,
  };
}

interface MockState {
  sections: ReturnType<typeof sectionsData>;
  history: ReturnType<typeof history>;
  restoreBodies: unknown[];
  restoreFailure?: string;
  diffUrls: URL[];
}

function newState(overrides: Partial<MockState> = {}): MockState {
  return {
    sections: sectionsData(),
    history: history(),
    restoreBodies: [],
    diffUrls: [],
    ...overrides,
  };
}

async function mockSections(page: Page, state: MockState) {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
  await page.route("**/api/knowledge-bases**", (route) =>
    route.fulfill(ok({ items: [], total: 0, limit: 100, offset: 0, has_next: false })),
  );
  await page.route(`**/api/documents/${DOC_ID}**`, async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith("/sections/revisions")) return route.fulfill(ok(state.history));
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
          revision({
            revision_no: 4,
            parent_revision_no: 3,
            kind: "restored",
            restored_from_revision_no: 1,
            is_current: true,
          }),
          ...state.history.revisions.map((row) => ({ ...row, is_current: false })),
        ],
        current_revision_no: 4,
      });
      state.sections = sectionsData({ revision: 4 });
      return route.fulfill(ok(state.sections));
    }
    if (path.endsWith("/diff")) {
      state.diffUrls.push(url);
      return route.fulfill(
        ok({
          revision_no: 3,
          against_revision_no: url.searchParams.get("against")
            ? Number(url.searchParams.get("against"))
            : 2,
          total: 3,
          offset: 0,
          limit: 25,
          changes: [
            {
              section_id: "nav-1",
              change: "changed",
              before: { title: "修正の前の章", level: 1, page_start: 1, page_end: 1 },
              after: { title: "修正の後の章", level: 2, page_start: 1, page_end: 2 },
            },
            {
              section_id: "manual-1",
              change: "added",
              before: null,
              after: { title: "足した章", level: 1, page_start: 2, page_end: 2 },
            },
            {
              section_id: "nav-3",
              change: "removed",
              before: { title: "消した章", level: 1, page_start: null, page_end: null },
              after: null,
            },
          ],
        }),
      );
    }
    if (path.endsWith("/sections")) return fulfillDocumentInspection(route, ok(state.sections));
    if (path.endsWith("/recipes")) return route.fulfill(ok([recipeView()]));
    if (path.endsWith("/ingestion-jobs") || path.endsWith("/ingestion-segments")) {
      return route.fulfill(ok([]));
    }
    if (path.endsWith("/chunk-sets") || path.endsWith("/chunks") || path.endsWith("/knowledge-bases")) {
      return route.fulfill(ok([]));
    }
    if (path.endsWith(`/documents/${DOC_ID}`)) return route.fulfill(ok(documentDetail()));
    if (path.endsWith("/extraction-view")) {
      return fulfillDocumentInspection(route, ok(documentDetail().extraction));
    }
    return route.fulfill({ status: 404, json: { data: null, error_messages: ["not found"], warning_messages: [] } });
  });
}

async function openPanel(page: Page) {
  await page.goto(`/documents/${DOC_ID}`);
  await page.getByRole("tab", { name: "章節ナビゲーション" }).click();
  return page.getByTestId("extraction-navigation");
}

async function openHistory(page: Page) {
  const panel = await openPanel(page);
  await panel.getByRole("button", { name: "修正の履歴" }).click();
  const sheet = page.getByRole("dialog", { name: "章節の修正の履歴" });
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

test.describe("章節の修正の履歴", { tag: "@responsive" }, () => {
  test("版の種類と今の版を区別して一覧できる", async ({ page }) => {
    await mockSections(page, newState());

    const sheet = await openHistory(page);

    const rows = sheet.getByRole("row");
    await expect(rows.filter({ hasText: "版 3" })).toContainText("今の版");
    await expect(rows.filter({ hasText: "版 3" })).toContainText("変更した章節 1 件");
    await expect(rows.filter({ hasText: "版 1" })).toContainText("抽出の章節");
    await expect(sheet).toContainText("直近 20 版を残します");
    await expectNoHorizontalOverflow(page);
  });

  test("差分は既定で親の版と比べ、比べる版を選べる", async ({ page }) => {
    const state = newState();
    await mockSections(page, state);

    const sheet = await openHistory(page);
    await sheet.getByTestId("section-revision-actions-3").getByRole("button").click();
    await page.getByRole("menuitem", { name: "差分を見る" }).click();

    const diff = sheet.getByTestId("section-revision-diff");
    await expect(diff).toContainText("修正の前の章");
    await expect(diff).toContainText("修正の後の章");
    await expect(diff).toContainText("階層 2 ・ p.1–2");
    await expect(diff).toContainText("足した章");
    await expect(diff).toContainText("消した章");
    expect(state.diffUrls[0]?.searchParams.get("against")).toBeNull();

    await diff.getByRole("combobox", { name: "比べる版" }).click();
    await page.getByRole("option", { name: /^版 1/ }).click();
    await expect.poll(() => state.diffUrls.at(-1)?.searchParams.get("against")).toBe("1");
    await expectNoHorizontalOverflow(page);
  });

  test("旧版への復元は確認の後に新しい版になる", async ({ page }) => {
    const state = newState();
    await mockSections(page, state);

    const sheet = await openHistory(page);
    await sheet.getByTestId("section-revision-actions-1").getByRole("button").click();
    await page.getByRole("menuitem", { name: "この版に戻す" }).click();
    const confirmDialog = page.getByRole("alertdialog");
    await expect(confirmDialog).toContainText("今の版は履歴に残ります");
    await confirmDialog.getByRole("button", { name: "この版に戻す" }).click();

    await expect
      .poll(() => state.restoreBodies)
      .toEqual([{ expected_revision: 3, recipe_id: expect.anything() }]);
    await expect(sheet.getByRole("row").filter({ hasText: "版 4" })).toContainText("版 1 から復元");
  });

  test("今の版には戻す操作を出さない", async ({ page }) => {
    await mockSections(page, newState());

    const sheet = await openHistory(page);
    await sheet.getByTestId("section-revision-actions-3").getByRole("button").click();

    await expect(page.getByRole("menuitem", { name: "差分を見る" })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "この版に戻す" })).toHaveCount(0);
  });

  test("ほかの保存が先に入った復元の失敗を表示する", async ({ page }) => {
    await mockSections(
      page,
      newState({ restoreFailure: "読み込んだ後に、ほかの操作で章節が変わりました。開き直してください。" }),
    );

    const sheet = await openHistory(page);
    await sheet.getByTestId("section-revision-actions-1").getByRole("button").click();
    await page.getByRole("menuitem", { name: "この版に戻す" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "この版に戻す" }).click();

    await expect(sheet).toContainText("ほかの操作で章節が変わりました");
  });

  test("抽出の章節が変わると修正の前提が変わったことを示す", async ({ page }) => {
    await mockSections(
      page,
      newState({
        sections: sectionsData({ base_changed: true, base_outline_changed: true }),
        history: history({
          revisions: [
            revision({
              revision_no: 3,
              parent_revision_no: 2,
              is_current: true,
              base_changed: true,
              base_outline_changed: true,
            }),
            revision({ revision_no: 2, parent_revision_no: 1, base_changed: true }),
            revision({ revision_no: 1, parent_revision_no: null, base_extraction_recipe_id: null }),
          ],
        }),
      }),
    );

    const panel = await openPanel(page);
    await expect(panel.getByTestId("sections-base-changed")).toContainText(
      "抽出の章節が変わったため、修正の前提が変わりました",
    );
    await expect(panel.getByTestId("sections-base-changed")).toContainText("自動では書き換えません");
    await expectNoPageOverflow(page);

    await panel.getByRole("button", { name: "修正の履歴" }).click();
    const rows = page.getByRole("dialog", { name: "章節の修正の履歴" }).getByRole("row");
    await expect(rows.filter({ hasText: "版 3" })).toContainText("今とは別の抽出の章節を基にしています");
    await expect(rows.filter({ hasText: "版 2" })).toContainText("今とは別の抽出を基にしています");
    await expect(rows.filter({ hasText: "版 1" })).toContainText("基にした抽出の記録がありません");
  });

  test("抽出の文だけが変わったときは章節が同じことを示す", async ({ page }) => {
    await mockSections(page, newState({ sections: sectionsData({ base_changed: true }) }));

    const panel = await openPanel(page);

    await expect(panel.getByTestId("sections-base-changed")).toContainText("基にした抽出が変わりました");
    await expect(panel.getByTestId("sections-base-changed")).toContainText("抽出の章節は同じです");
  });

  test("修正の無い文書にも履歴の入口があり、版が無いと空の案内を出す", async ({ page }) => {
    await mockSections(
      page,
      newState({
        sections: sectionsData({ source: "extraction", revision: null }),
        history: history({ revisions: [], current_revision_no: null }),
      }),
    );

    const sheet = await openHistory(page);

    await expect(sheet).toContainText("まだ修正を保存していません");
    await expect(page.getByTestId("sections-base-changed")).toHaveCount(0);
  });
});
