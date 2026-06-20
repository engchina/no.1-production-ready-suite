import { expect, type Page, test } from "@playwright/test";
import { mockDatabaseReady } from "./_helpers";

// 2 段階ファイル処理(parse → 人がプレビュー確認 → index)の REVIEW ゲート UI を検証する。
// 文書状態が REVIEW のとき、DocumentWorkspace に「承認して索引」「却下」導線と
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

function indexJob() {
  return {
    id: "job-index-1",
    document_id: DOC_ID,
    status: "QUEUED",
    phase: "INDEX",
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
    reject: number;
    approveBody: unknown;
  } = { approve: 0, reject: 0, approveBody: null };
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
    await route.fulfill({ json: { data: indexJob(), error_messages: [], warning_messages: [] } });
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
  await page.route(`**/api/documents/${DOC_ID}/ingestion-segments`, (route) =>
    route.fulfill({ json: { data: [], error_messages: [], warning_messages: [] } })
  );
  await page.route(`**/api/documents/${DOC_ID}/ingestion-jobs**`, (route) =>
    route.fulfill({ json: { data: indexJob(), error_messages: [], warning_messages: [] } })
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

  await expect(page.getByText("確認待ち").first()).toBeVisible();
  await expect(
    page.getByText("承認した文書のみ RAG 検索の対象になります", { exact: false })
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "承認して索引" })).toBeVisible();
  await expect(page.getByRole("button", { name: "却下" })).toBeVisible();
});

test("承認すると index job を投入し成功 toast を出す", async ({ page }) => {
  const calls = await mockReviewWorkspace(page);
  await page.goto(`/documents/${DOC_ID}`);

  await page.getByRole("button", { name: "承認して索引" }).click();

  await expect(page.getByText("承認しました。索引を開始します。")).toBeVisible();
  expect(calls.approve).toBe(1);
});

test("抽出テキストを修正して承認すると編集差分を送信する", async ({ page }) => {
  const calls = await mockReviewWorkspace(page);
  await page.goto(`/documents/${DOC_ID}`);

  await page.getByRole("button", { name: "抽出テキストを修正" }).click();
  const editor = page.getByLabel("本文テキスト");
  await expect(editor).toBeVisible();

  const elementField = page.locator("#review-edit-el-0000");
  await elementField.fill("経費申請(編集後)");

  await page.getByRole("button", { name: "承認して索引" }).click();
  await expect(page.getByText("承認しました。索引を開始します。")).toBeVisible();

  expect(calls.approve).toBe(1);
  const body = calls.approveBody as { element_edits: { element_id: string; text: string }[] };
  expect(body.element_edits).toContainEqual({
    element_id: "el-0000",
    text: "経費申請(編集後)",
  });
});

test("表セルを修正して承認すると table_cell_edits を送信する", async ({ page }) => {
  const calls = await mockReviewWorkspace(page);
  await page.goto(`/documents/${DOC_ID}`);

  await page.getByRole("button", { name: "抽出テキストを修正" }).click();
  const cellField = page.locator("#review-edit-cell-tbl-1\\:\\:1\\:\\:1");
  await expect(cellField).toBeVisible();
  await cellField.fill("2000円");

  await page.getByRole("button", { name: "承認して索引" }).click();
  await expect(page.getByText("承認しました。索引を開始します。")).toBeVisible();

  const body = calls.approveBody as {
    table_cell_edits: { table_id: string; row: number; col: number; text: string }[];
  };
  expect(body.table_cell_edits).toContainEqual({
    table_id: "tbl-1",
    row: 1,
    col: 1,
    text: "2000円",
  });
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
