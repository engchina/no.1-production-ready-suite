import { expect, type Page, test } from "@playwright/test";
import { mockDatabaseReady } from "./_helpers";

// 段階レビュー可能なファイル処理(EXTRACT → CHUNK → INDEX)の REVIEW ゲート UI を検証する。
// 文書状態が REVIEW のとき、DocumentWorkspace に「承認して Chunk 作成」「却下」導線と
// 確認待ち Banner が出ること、承認/却下の操作フィードバック(toast)を確認する。

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

const DOC_ID = "doc-review";

function reviewDocumentDetail(status = "REVIEW") {
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
    error_message: null,
    extraction: {
      raw_text: "経費申請\n交通費は1000円です。",
      document_type: "規程",
      confidence: 0.98,
      warnings: [],
      pages: [{ page_number: 1, width: 612, height: 792, element_ids: ["el-0000"] }],
      elements: [
        {
          kind: "title",
          text: "経費申請",
          order: 0,
          element_id: "el-0000",
          content_kind: "text",
          source_parser: "local_text_structure",
          page_number: 1,
          bbox: [0, 0, 100, 40],
          section_path: ["経費申請"],
          confidence: 0.98,
          metadata: {},
        },
        {
          kind: "table",
          text: "料金表\n| 項目 | 値 |\n| 交通費 | 1000円 |",
          order: 1,
          element_id: "tbl-1",
          content_kind: "table",
          source_parser: "local_text_structure",
          page_number: 1,
          bbox: [0, 40, 100, 80],
          section_path: ["経費申請"],
          confidence: 0.98,
          metadata: { table_id: "tbl-1" },
        },
      ],
      tables: [
        {
          table_id: "tbl-1",
          element_id: "tbl-1",
          page_number: 1,
          caption: "料金表",
          metadata: {},
          cells: [
            { row: 0, col: 0, text: "項目", row_span: 1, col_span: 1, page_number: 1, bbox: null, confidence: null, metadata: {} },
            { row: 0, col: 1, text: "値", row_span: 1, col_span: 1, page_number: 1, bbox: null, confidence: null, metadata: {} },
            { row: 1, col: 0, text: "交通費", row_span: 1, col_span: 1, page_number: 1, bbox: null, confidence: null, metadata: {} },
            { row: 1, col: 1, text: "1000円", row_span: 1, col_span: 1, page_number: 1, bbox: null, confidence: null, metadata: {} },
          ],
        },
      ],
      assets: [],
    },
    source_profile: {
      original_file_name: "policy.txt",
      sanitized_file_name: "policy.txt",
      extension: ".txt",
      content_type: "text/plain",
      inferred_content_type: "text/plain",
      file_size_bytes: 64,
      content_sha256: "a".repeat(64),
      modality: "text",
      parser_profile: "local_text_structure",
      parser_backend: "local_partition",
      parser_version: "v1",
      preview_kind: "text",
      text_charset: "utf-8",
      duplicate_of_document_id: null,
      unsupported_reason: null,
      quality_status: "ready",
      quality_warnings: [],
    },
    knowledge_bases: [{ id: "kb-1", name: "社内規程" }],
  };
}

function chunkJob() {
  return {
    id: "job-chunk-1",
    document_id: DOC_ID,
    status: "QUEUED",
    phase: "CHUNK",
    parser_profile: "local_text_structure",
    quality_warnings: [],
    skip_reason: null,
    error_message: null,
    attempt_count: 0,
    max_attempts: 3,
    queued_at: "2026-06-18T00:00:05Z",
    started_at: null,
    finished_at: null,
  };
}

async function mockReviewWorkspace(page: Page) {
  const calls: {
    approve: number;
    save: number;
    reject: number;
    approveBody: unknown;
    saveBody: unknown;
  } = { approve: 0, save: 0, reject: 0, approveBody: null, saveBody: null };
  await mockDatabaseReady(page);
  await page.route("**/api/auth/me", (route) => route.fulfill({ json: authStatus }));
  await page.route("**/api/knowledge-bases**", (route) =>
    route.fulfill({
      json: {
        data: { items: [{ id: "kb-1", name: "社内規程", document_count: 1 }], total: 1, limit: 100, offset: 0, has_next: false },
        error_messages: [],
        warning_messages: [],
      },
    })
  );
  await page.route(`**/api/documents/${DOC_ID}/approve`, async (route) => {
    calls.approve += 1;
    const post = route.request().postData();
    calls.approveBody = post ? JSON.parse(post) : null;
    await route.fulfill({ json: { data: chunkJob(), error_messages: [], warning_messages: [] } });
  });
  await page.route(`**/api/documents/${DOC_ID}/review-edits`, async (route) => {
    calls.save += 1;
    const body = route.request().postDataJSON() as {
      element_edits?: { element_id: string; text: string }[];
      table_cell_edits?: { table_id: string; row: number; col: number; text: string }[];
    };
    calls.saveBody = body;
    const detail = reviewDocumentDetail("REVIEW");
    for (const edit of body.element_edits ?? []) {
      const element = detail.extraction.elements.find((item) => item.element_id === edit.element_id);
      if (element) element.text = edit.text;
    }
    for (const edit of body.table_cell_edits ?? []) {
      const table = detail.extraction.tables.find((item) => item.table_id === edit.table_id);
      const cell = table?.cells.find((item) => item.row === edit.row && item.col === edit.col);
      if (cell) cell.text = edit.text;
    }
    detail.extraction.raw_text = detail.extraction.elements.map((item) => item.text).join("\n");
    await route.fulfill({
      json: { data: detail, error_messages: [], warning_messages: [] },
    });
  });
  await page.route(`**/api/documents/${DOC_ID}/reject`, async (route) => {
    calls.reject += 1;
    await route.fulfill({
      json: { data: reviewDocumentDetail("UPLOADED"), error_messages: [], warning_messages: [] },
    });
  });
  await page.route(`**/api/documents/${DOC_ID}/chunks`, (route) =>
    route.fulfill({ json: { data: [], error_messages: [], warning_messages: [] } })
  );
  await page.route(`**/api/documents/${DOC_ID}/chunk-sets`, (route) =>
    route.fulfill({ json: { data: [], error_messages: [], warning_messages: [] } })
  );
  await page.route(`**/api/documents/${DOC_ID}/ingestion-config`, (route) =>
    route.fulfill({
      json: {
        data: {
          document_id: DOC_ID,
          is_indexed: false,
          owning_knowledge_base: { id: "kb-1", name: "社内規程" },
          effective_chunking_strategy: "structure_aware",
          effective_parser_adapter_backend: "local_text_structure",
          observed_chunking_strategy: null,
          observed_parser_backend: null,
          chunking_drift: false,
          parser_drift: false,
          config_drift: false,
        },
        error_messages: [],
        warning_messages: [],
      },
    })
  );
  await page.route(`**/api/documents/${DOC_ID}/ingestion-segments`, (route) =>
    route.fulfill({ json: { data: [], error_messages: [], warning_messages: [] } })
  );
  await page.route(`**/api/documents/${DOC_ID}/ingestion-jobs**`, (route) =>
    route.fulfill({ json: { data: [], error_messages: [], warning_messages: [] } })
  );
  await page.route(`**/api/documents/ingestion-jobs/job-chunk-1`, (route) =>
    route.fulfill({ json: { data: chunkJob(), error_messages: [], warning_messages: [] } })
  );
  await page.route(`**/api/documents/${DOC_ID}/knowledge-bases`, (route) =>
    route.fulfill({ json: { data: [{ id: "kb-1", name: "社内規程" }], error_messages: [], warning_messages: [] } })
  );
  await page.route(`**/api/documents/${DOC_ID}/extraction-export**`, (route) =>
    route.fulfill({
      json: {
        data: {
          document_id: DOC_ID,
          file_name: "policy.txt",
          format: "markdown",
          content_type: "text/markdown; charset=utf-8",
          content: "# 経費申請\n\n交通費は1000円です。",
          payload: {},
          chunks: [],
          parser_backend: "local_partition",
          parser_profile: "local_text_structure",
          page_count: 1,
          element_count: 1,
          table_count: 0,
          asset_count: 0,
        },
        error_messages: [],
        warning_messages: [],
      },
    })
  );
  await page.route(`**/api/documents/${DOC_ID}/content`, (route) =>
    route.fulfill({ status: 200, contentType: "text/plain", body: "経費申請\n交通費は1000円です。" })
  );
  // 詳細はテストごとに上書きできるよう最後に登録。
  await page.route(`**/api/documents/${DOC_ID}`, (route) =>
    route.fulfill({ json: { data: reviewDocumentDetail("REVIEW"), error_messages: [], warning_messages: [] } })
  );
  return calls;
}

test("REVIEW 文書は確認待ち表示と承認/却下導線を出す", async ({ page }) => {
  await mockReviewWorkspace(page);
  await page.goto(`/documents/${DOC_ID}`);

  await expect(page.getByRole("tab", { name: "本文テキスト" })).toHaveAttribute(
    "aria-selected",
    "true"
  );
  await expect(page.getByRole("button", { name: "本文を修正" })).toHaveCount(0);
  await expect(page.locator("#review-edit-raw-text")).toHaveCount(0);
  await expect(page.getByText("確認待ち").first()).toBeVisible();
  await expect(
    page.getByText("内容を確認し、問題なければ Chunk 作成へ進めてください", { exact: false })
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "承認して Chunk 作成" })).toBeVisible();
  await expect(page.getByRole("button", { name: "却下" })).toBeVisible();
});

test("承認すると chunk job を投入し成功 toast を出す", async ({ page }) => {
  const calls = await mockReviewWorkspace(page);
  await page.goto(`/documents/${DOC_ID}`);

  await page.getByRole("button", { name: "承認して Chunk 作成" }).click();

  await expect(page.getByText("承認しました。次の処理を開始します。")).toBeVisible();
  expect(calls.approve).toBe(1);
});

test("構造化要素の修正を保持し、保存後だけ承認できる", async ({ page }) => {
  const calls = await mockReviewWorkspace(page);
  await page.goto(`/documents/${DOC_ID}`);

  await page.getByRole("tab", { name: "構造化要素" }).click();
  await expect(page.locator("#review-edit-raw-text")).toHaveCount(0);
  await page.getByRole("button", { name: "構造化要素を修正" }).click();
  await expect(page.locator("#review-edit-raw-text")).toHaveCount(0);
  await expect(page.locator("#review-edit-tbl-1")).toHaveCount(0);

  const elementField = page.locator("#review-edit-el-0000");
  await elementField.fill("経費申請(編集後)");
  const discardButton = page.getByRole("button", { name: "変更を破棄" });
  const saveButton = page.getByRole("button", { name: "変更を保存" });
  const closeButton = page.getByRole("button", { name: "編集を閉じる" });
  await expect(discardButton).toBeVisible();
  await expect(saveButton).toBeVisible();
  await expect(closeButton).toBeVisible();
  if ((page.viewportSize()?.width ?? 0) >= 1280) {
    const [discardBox, saveBox, closeBox] = await Promise.all([
      discardButton.boundingBox(),
      saveButton.boundingBox(),
      closeButton.boundingBox(),
    ]);
    expect(discardBox?.x).toBeLessThan(saveBox?.x ?? 0);
    expect(saveBox?.x).toBeLessThan(closeBox?.x ?? 0);
  }
  expect(
    await page.getByRole("tabpanel").evaluate((element) => element.scrollWidth > element.clientWidth)
  ).toBe(false);
  await page.getByRole("button", { name: "編集を閉じる" }).click();
  await expect(
    page.getByText("未保存の変更があります。変更を保存してから承認してください。")
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "承認して Chunk 作成" })).toBeDisabled();

  await page.getByRole("tab", { name: "本文テキスト" }).click();
  await expect(page.getByRole("button", { name: "本文を修正" })).toHaveCount(0);
  await page.getByRole("tab", { name: "構造化要素" }).click();
  await page.getByRole("button", { name: "構造化要素を修正" }).click();
  await expect(page.locator("#review-edit-el-0000")).toHaveValue("経費申請(編集後)");
  await expect(page.getByRole("button", { name: "変更を保存" })).toHaveCount(1);
  await page.getByRole("button", { name: "変更を保存" }).click();
  await expect(page.getByText("変更を保存しました。")).toBeVisible();
  await expect(page.getByText("未保存の変更があります。", { exact: false })).toHaveCount(0);
  await expect(page.locator("#review-edit-el-0000")).toHaveValue("経費申請(編集後)");
  expect(calls.save).toBe(1);
  const saveBody = calls.saveBody as {
    element_edits: { element_id: string; text: string }[];
  };
  expect(saveBody.element_edits).toContainEqual({
    element_id: "el-0000",
    text: "経費申請(編集後)",
  });

  await expect(page.getByRole("button", { name: "承認して Chunk 作成" })).toBeEnabled();
  await page.getByRole("button", { name: "承認して Chunk 作成" }).click();
  await expect(page.getByText("承認しました。次の処理を開始します。")).toBeVisible();

  expect(calls.approve).toBe(1);
  expect(calls.approveBody).toBeNull();
});

test("表セルの修正を下書き保存すると table_cell_edits を送信する", async ({ page }) => {
  const calls = await mockReviewWorkspace(page);
  await page.goto(`/documents/${DOC_ID}`);

  await page.getByRole("tab", { name: "構造化要素" }).click();
  await page.getByRole("button", { name: "構造化要素を修正" }).click();
  const cellField = page.locator("#review-edit-cell-tbl-1\\:\\:1\\:\\:1");
  await expect(cellField).toBeVisible();
  await cellField.fill("2000円");

  await page.getByRole("button", { name: "変更を保存" }).click();
  await expect(page.getByText("変更を保存しました。")).toBeVisible();

  const body = calls.saveBody as {
    table_cell_edits: { table_id: string; row: number; col: number; text: string }[];
  };
  expect(body.table_cell_edits).toContainEqual({
    table_id: "tbl-1",
    row: 1,
    col: 1,
    text: "2000円",
  });
  expect(calls.approve).toBe(0);
});

test("下書き保存に失敗しても入力と未保存状態を保持する", async ({ page }) => {
  await mockReviewWorkspace(page);
  await page.route(`**/api/documents/${DOC_ID}/review-edits`, (route) =>
    route.fulfill({
      status: 500,
      json: {
        data: null,
        error_messages: ["Oracleへの保存に失敗しました。"],
        warning_messages: [],
      },
    })
  );
  await page.goto(`/documents/${DOC_ID}`);

  await page.getByRole("tab", { name: "構造化要素" }).click();
  await page.getByRole("button", { name: "構造化要素を修正" }).click();
  const elementField = page.locator("#review-edit-el-0000");
  await elementField.fill("保存失敗後も残る修正");
  await page.getByRole("button", { name: "変更を保存" }).click();

  await expect(page.getByText("Oracleへの保存に失敗しました。")).toBeVisible();
  await expect(elementField).toHaveValue("保存失敗後も残る修正");
  await expect(page.getByRole("button", { name: "承認して Chunk 作成" })).toBeDisabled();
});

test("未保存変更は確認後に破棄し、保存済み表示へ戻せる", async ({ page }) => {
  const calls = await mockReviewWorkspace(page);
  await page.goto(`/documents/${DOC_ID}`);

  await page.getByRole("tab", { name: "構造化要素" }).click();
  await page.getByRole("button", { name: "構造化要素を修正" }).click();
  const elementField = page.locator("#review-edit-el-0000");
  await elementField.fill("破棄する変更");

  await page.getByRole("button", { name: "変更を破棄" }).click();
  let dialog = page.getByRole("alertdialog");
  await expect(dialog.getByText("未保存の変更を破棄しますか?")).toBeVisible();
  await dialog.getByRole("button", { name: "キャンセル" }).click();
  await expect(elementField).toHaveValue("破棄する変更");
  await expect(page.getByRole("button", { name: "承認して Chunk 作成" })).toBeDisabled();

  await page.getByRole("button", { name: "変更を破棄" }).click();
  dialog = page.getByRole("alertdialog");
  await dialog.getByRole("button", { name: "変更を破棄" }).click();

  await expect(page.locator("#review-edit-el-0000")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "構造化要素を修正" })).toBeVisible();
  await expect(page.getByRole("button", { name: "承認して Chunk 作成" })).toBeEnabled();
  expect(calls.save).toBe(0);
});

test("未保存変更がある状態でページを離れると破棄確認を出す", async ({ page }) => {
  await mockReviewWorkspace(page);
  await page.goto(`/documents/${DOC_ID}`);

  await page.getByRole("tab", { name: "構造化要素" }).click();
  await page.getByRole("button", { name: "構造化要素を修正" }).click();
  await page.locator("#review-edit-el-0000").fill("未保存の変更");
  await page.getByRole("link", { name: "一覧へ戻る" }).click();

  const dialog = page.getByRole("alertdialog");
  await expect(dialog.getByText("未保存の変更を破棄しますか?")).toBeVisible();
  await dialog.getByRole("button", { name: "キャンセル" }).click();
  await expect(page).toHaveURL(new RegExp(`/documents/${DOC_ID}$`));
  await expect(page.locator("#review-edit-el-0000")).toHaveValue("未保存の変更");
});

test("却下は確認ダイアログを挟み、確定で UPLOADED へ戻す", async ({ page }) => {
  const calls = await mockReviewWorkspace(page);
  await page.goto(`/documents/${DOC_ID}`);

  await page.getByRole("button", { name: "却下" }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("この抽出結果を却下しますか?")).toBeVisible();

  // キャンセルでは却下しない。
  await dialog.getByRole("button", { name: "キャンセル" }).click();
  expect(calls.reject).toBe(0);

  // 再度開いて確定すると却下する。
  await page.getByRole("button", { name: "却下" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "却下" }).click();
  await expect(page.getByText("却下しました。アップロード済みに戻しました。")).toBeVisible();
  expect(calls.reject).toBe(1);
});
