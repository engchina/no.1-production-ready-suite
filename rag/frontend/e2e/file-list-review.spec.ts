import { expect, type Page, test } from "@playwright/test";
import { mockDatabaseReady, mockLocalAuth } from "./_helpers";

// Issue 281: 文書インデックスの不具合（範囲外のページ・投入結果の通知・検索欄の blur・状態の絞り込み・
// 削除の後始末の警告）の回帰。desktop / mobile の両 project で動く。

type FileStatus = "UPLOADED" | "PREPROCESSED" | "INDEXED" | "ERROR";

interface DocumentSummary {
  id: string;
  file_name: string;
  status: FileStatus;
  category_name: string | null;
  content_type: string | null;
  file_size_bytes: number | null;
  content_sha256: string | null;
  duplicate_of_document_id: string | null;
  uploaded_at: string;
  indexed_at: string | null;
  knowledge_bases: { id: string; name: string }[];
  source_profile: null;
}

interface MockOptions {
  onEnqueue?: (id: string) => { status: number; json: unknown };
  deleteWarnings?: string[];
}

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
});

test("最後のページの最後の 1 件を削除したら、1 つ前のページへ戻る", async ({ page }) => {
  const documents = Array.from({ length: 21 }, (_, index) =>
    documentSummary(`doc-${index + 1}`, `file-${String(index + 1).padStart(2, "0")}.txt`, "INDEXED")
  );
  await mockFileListApi(page, documents);

  await page.goto("/file-list");
  // 共通の Pagination（10 件/ページ、#265）。3 ページ目へ移る。
  const pagination = page.getByTestId("file-list-pagination");
  await pagination.getByRole("button", { name: "次へ" }).click();
  await pagination.getByRole("button", { name: "次へ" }).click();
  await expect(pagination).toContainText("21 - 21 / 21 件");

  const last = documents[20];
  await page.getByRole("button", { name: `${last.file_name} の操作` }).click();
  await page.getByRole("menuitem", { name: `${last.file_name} を削除` }).click();
  await page
    .getByRole("alertdialog", { name: "このドキュメントを削除しますか？" })
    .getByRole("button", { name: "削除" })
    .click();

  // 空の 3 ページ目に「該当なし」とだけ出してページ送りが消える状態にしない。
  await expect(pagination).toContainText("11 - 20 / 20 件");
  await expect(page.getByRole("link", { name: "file-11.txt" })).toBeVisible();
  await expect(page.getByText("該当するドキュメントがありません。")).toHaveCount(0);
});

test("ファイル準備の投入の失敗・スキップを通知する", async ({ page }) => {
  const documents = [
    documentSummary("doc-queued", "queued.txt", "UPLOADED"),
    documentSummary("doc-dup", "duplicate.txt", "UPLOADED"),
  ];
  await mockFileListApi(page, documents, {
    onEnqueue: (id) =>
      id === "doc-queued"
        ? {
            status: 409,
            json: {
              data: null,
              error_messages: [
                "このドキュメントは取込待ちまたは取込中です。完了してから再実行してください。",
              ],
              warning_messages: [],
            },
          }
        : {
            status: 200,
            json: {
              data: ingestionJob(id, "SKIPPED", "duplicate_content"),
              error_messages: [],
              warning_messages: [],
            },
          },
  });

  await page.goto("/file-list");

  await page.getByRole("button", { name: "queued.txt の操作" }).click();
  await page.getByRole("menuitem", { name: "ファイル準備を実行" }).click();
  await expect(
    page.getByText("「queued.txt」のファイル準備を開始できませんでした。").first()
  ).toBeVisible();
  await expect(
    page.getByText("このドキュメントは取込待ちまたは取込中です。完了してから再実行してください。").first()
  ).toBeVisible();

  await page.getByRole("button", { name: "duplicate.txt の操作" }).click();
  await page.getByRole("menuitem", { name: "ファイル準備を実行" }).click();
  await expect(
    page.getByText("「duplicate.txt」はファイル準備をスキップしました。").first()
  ).toBeVisible();
  await expect(page.getByText("同一内容の文書が既に登録されています。").first()).toBeVisible();
});

test("一括投入の部分失敗をまとめて通知する", async ({ page }) => {
  const documents = [
    documentSummary("doc-ok", "ok.txt", "UPLOADED"),
    documentSummary("doc-ng", "ng.txt", "ERROR"),
  ];
  await mockFileListApi(page, documents, {
    onEnqueue: (id) =>
      id === "doc-ok"
        ? {
            status: 200,
            json: { data: ingestionJob(id, "QUEUED"), error_messages: [], warning_messages: [] },
          }
        : {
            status: 409,
            json: {
              data: null,
              error_messages: ["このドキュメントは現在取込中です。"],
              warning_messages: [],
            },
          },
  });

  await page.goto("/file-list");
  await page.getByRole("checkbox", { name: "すべて選択" }).check();
  await page.getByRole("button", { name: "一括投入 (2)" }).click();

  await expect(
    page.getByText("2 件中 1 件のファイル準備を開始しました。開始できなかった文書があります。").first()
  ).toBeVisible();
  await expect(page.getByText(/失敗 1 件。このドキュメントは現在取込中です。/).first()).toBeVisible();
});

test("検索欄から focus を外しただけでは選択とページを解除しない", async ({ page }) => {
  const documents = Array.from({ length: 21 }, (_, index) =>
    documentSummary(`doc-${index + 1}`, `file-${String(index + 1).padStart(2, "0")}.txt`, "UPLOADED")
  );
  await mockFileListApi(page, documents);

  await page.goto("/file-list");
  const pagination = page.getByTestId("file-list-pagination");
  await pagination.getByRole("button", { name: "次へ" }).click();
  await pagination.getByRole("button", { name: "次へ" }).click();
  await expect(pagination).toContainText("21 - 21 / 21 件");
  await page.locator("tbody tr").filter({ hasText: "file-21.txt" }).getByRole("checkbox").check();
  await expect(page.getByText("1 件選択中")).toBeVisible();

  const search = page.getByRole("textbox", { name: "ファイル名で検索" });
  await search.focus();
  await search.blur();

  await expect(page.getByText("1 件選択中")).toBeVisible();
  await expect(pagination).toContainText("21 - 21 / 21 件");
  await expect(search).toHaveAttribute("maxlength", "200");
});

test("ファイル準備確認待ちで絞り込める", async ({ page }) => {
  const documents = [
    documentSummary("doc-pre", "prepared.pdf", "PREPROCESSED"),
    documentSummary("doc-idx", "indexed.pdf", "INDEXED"),
  ];
  const requested: (string | null)[] = [];
  await mockFileListApi(page, documents, {}, (url) => requested.push(url.searchParams.get("status")));

  await page.goto("/file-list");
  await page.getByRole("button", { name: "ファイル準備確認待ち" }).click();

  await expect(page.getByRole("link", { name: "prepared.pdf" })).toBeVisible();
  await expect(page.getByRole("link", { name: "indexed.pdf" })).toHaveCount(0);
  expect(requested).toContain("PREPROCESSED");
});

test("削除の後始末に失敗した警告を成功として黙らせない", async ({ page }) => {
  const documents = [documentSummary("doc-1", "policy.txt", "INDEXED")];
  await mockFileListApi(page, documents, {
    deleteWarnings: [
      "文書は削除しましたが、原本ファイルの削除に失敗しました。保存先を確認してください。",
    ],
  });

  await page.goto("/file-list");
  await page.getByRole("button", { name: "policy.txt の操作" }).click();
  await page.getByRole("menuitem", { name: "policy.txt を削除" }).click();
  await page
    .getByRole("alertdialog", { name: "このドキュメントを削除しますか？" })
    .getByRole("button", { name: "削除" })
    .click();

  await expect(
    page
      .getByText("「policy.txt」を削除しましたが、保存先のファイルの後始末に一部失敗しました。")
      .first()
  ).toBeVisible();
  await expect(
    page.getByText("原本ファイルの削除に失敗しました。保存先を確認してください。").first()
  ).toBeVisible();
});

async function mockFileListApi(
  page: Page,
  documents: DocumentSummary[],
  options: MockOptions = {},
  onList?: (url: URL) => void
) {
  await page.route("**/api/knowledge-bases**", async (route) => {
    await route.fulfill({
      json: {
        data: { items: [], total: 0, limit: 100, offset: 0, has_next: false },
        error_messages: [],
        warning_messages: [],
      },
    });
  });

  await page.route("**/api/documents**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const parts = url.pathname.split("/").filter(Boolean);

    if (request.method() === "GET" && url.pathname === "/api/documents") {
      onList?.(url);
      const status = url.searchParams.get("status");
      const limit = Number(url.searchParams.get("limit") ?? "10");
      const offset = Number(url.searchParams.get("offset") ?? "0");
      const filtered = documents.filter((document) => !status || document.status === status);
      await route.fulfill({
        json: {
          data: {
            items: filtered.slice(offset, offset + limit),
            total: filtered.length,
            limit,
            offset,
            has_next: offset + limit < filtered.length,
          },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }

    if (request.method() === "POST" && parts[3] === "ingestion-jobs") {
      const result = options.onEnqueue?.(parts[2]) ?? {
        status: 200,
        json: { data: ingestionJob(parts[2], "QUEUED"), error_messages: [], warning_messages: [] },
      };
      await route.fulfill(result);
      return;
    }

    if (request.method() === "DELETE" && parts.length === 3) {
      const id = parts[2];
      const index = documents.findIndex((item) => item.id === id);
      const document = documents[index];
      documents.splice(index, 1);
      await route.fulfill({
        json: {
          data: {
            id,
            file_name: document.file_name,
            object_storage_path: `local://uploaded/${document.file_name}`,
            object_deleted: (options.deleteWarnings ?? []).length === 0,
            artifact_deleted_count: 0,
            artifact_delete_failed_count: 0,
          },
          error_messages: [],
          warning_messages: options.deleteWarnings ?? [],
        },
      });
      return;
    }

    // 投入後の再取得（ジョブ一覧など）は空で返す。
    await route.fulfill({ json: { data: [], error_messages: [], warning_messages: [] } });
  });
}

function ingestionJob(documentId: string, status: string, skipReason: string | null = null) {
  return {
    id: `job-${documentId}`,
    document_id: documentId,
    recipe_id: null,
    recipe_revision: null,
    status,
    phase: "PREPROCESS",
    parser_profile: "local_text_structure",
    quality_warnings: [],
    skip_reason: skipReason,
    error_message: null,
    attempt_count: 0,
    max_attempts: 3,
    queued_at: "2026-09-28T00:00:00Z",
    started_at: null,
    finished_at: status === "SKIPPED" ? "2026-09-28T00:00:00Z" : null,
  };
}

function documentSummary(id: string, fileName: string, status: FileStatus): DocumentSummary {
  return {
    id,
    file_name: fileName,
    status,
    category_name: "社内規程",
    content_type: "text/plain",
    file_size_bytes: 1024,
    content_sha256: "a".repeat(64),
    duplicate_of_document_id: null,
    uploaded_at: "2026-06-16T09:00:00Z",
    indexed_at: status === "INDEXED" ? "2026-06-16T09:05:00Z" : null,
    knowledge_bases: [],
    source_profile: null,
  };
}
