import { expect, test, type Page, type Route } from "@playwright/test";

import {
  apiEnvelope,
  expectNoPageOverflow,
  mockAuthUser,
  mockDatabaseReady,
  mockLocalAuth,
} from "./_helpers";

// 文書アップロードのレビューで直した点（#280）。
// - 一括アップロードは 1 リクエストの合計を 1 ファイルの上限以内に分けて送り、上限超過は送らない
// - 送信中は経過時間付きで示し、KB 一覧の読み込み中は Skeleton で領域を確保する
// - 設定・知識ベース管理を開けない利用者には導線を出さない
// - 先頭ページ（50 件）を超える KB も選べる

function knowledgeBase(index: number) {
  return {
    id: `kb-${index}`,
    name: `知識ベース ${String(index).padStart(3, "0")}`,
    description: null,
    status: "ACTIVE",
    default_search_mode: "hybrid",
    document_count: 0,
    indexed_document_count: 0,
    error_document_count: 0,
    searchable_chunk_count: 0,
    created_at: "2026-06-15T00:00:00Z",
    updated_at: "2026-06-15T00:00:00Z",
    archived_at: null,
  };
}

function sourceProfile(fileName: string) {
  return {
    original_file_name: fileName,
    sanitized_file_name: fileName,
    extension: ".txt",
    content_type: "text/plain",
    inferred_content_type: "text/plain",
    file_size_bytes: 6,
    content_sha256: "b".repeat(64),
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
  };
}

function uploadResult(fileName: string, duplicateOf: string | null = null) {
  return {
    id: `doc-${fileName}`,
    file_name: fileName,
    status: "UPLOADED",
    file_size_bytes: 6,
    content_sha256: "b".repeat(64),
    duplicate_of_document_id: duplicateOf,
    knowledge_bases: [],
    source_profile: sourceProfile(fileName),
    ingestion_started: false,
    ingestion_job: null,
  };
}

async function mockUploadPage(
  page: Page,
  {
    maxUploadBytes = 200 * 1024 * 1024,
    knowledgeBaseCount = 1,
    knowledgeBases,
  }: {
    maxUploadBytes?: number;
    knowledgeBaseCount?: number;
    knowledgeBases?: (route: Route) => Promise<void>;
  } = {}
) {
  await mockDatabaseReady(page);
  await page.route("**/api/settings/upload-storage", (route) =>
    route.fulfill({
      json: apiEnvelope({
        backend: "local",
        local_storage_dir: "/tmp/rag-uploads",
        object_storage_namespace: null,
        object_storage_bucket: null,
        object_storage_region: null,
        readiness: "ready",
        source: "runtime",
        max_upload_bytes: maxUploadBytes,
      }),
    })
  );
  const all = Array.from({ length: knowledgeBaseCount }, (_, index) => knowledgeBase(index + 1));
  await page.route(
    "**/api/knowledge-bases**",
    knowledgeBases ??
      ((route) => {
        const url = new URL(route.request().url());
        const limit = Number(url.searchParams.get("limit") ?? 50);
        const offset = Number(url.searchParams.get("offset") ?? 0);
        return route.fulfill({
          json: apiEnvelope({
            items: all.slice(offset, offset + limit),
            total: all.length,
            limit,
            offset,
            has_next: offset + limit < all.length,
          }),
        });
      })
  );
  await page.route("**/api/documents/ingestion-jobs**", (route) =>
    route.fulfill({ json: apiEnvelope({ items: [], total: 0, limit: 5, offset: 0, has_next: false }) })
  );
}

function textFile(name: string, size: number) {
  return { name, mimeType: "text/plain", buffer: Buffer.alloc(size, "a") };
}

test("一括アップロードは上限以内に分けて送り、上限を超えるファイルは送らない", async ({ page }) => {
  await mockLocalAuth(page);
  await mockUploadPage(page, { maxUploadBytes: 10 });
  const requests: string[][] = [];
  await page.route("**/api/documents/batch-upload", async (route) => {
    const body = route.request().postData() ?? "";
    const names = [...body.matchAll(/filename="([^"]+)"/g)].map((match) => match[1]);
    requests.push(names);
    await route.fulfill({
      json: apiEnvelope({
        items: names.map((name) => uploadResult(name, name === "c.txt" ? "doc-a.txt" : null)),
        failed_items: [],
        total_count: names.length,
        uploaded_count: names.length,
        failed_count: 0,
        queued_count: 0,
        skipped_count: 0,
      }),
    });
  });

  await page.goto("/upload");
  await expect(page.getByText("最大 10 B / ファイル")).toBeVisible();
  await page.locator('input[type="file"]').setInputFiles([
    textFile("a.txt", 6),
    textFile("b.txt", 4),
    textFile("huge.txt", 11),
    textFile("c.txt", 6),
  ]);

  const summary = page.getByRole("heading", { name: "アップロード結果" });
  await expect(summary).toBeVisible();
  // 合計が 10 B 以内のまとまりごとに送り、11 B のファイルは送らない。
  expect(requests).toEqual([["a.txt", "b.txt"], ["c.txt"]]);
  await expect(page.getByText("ファイルサイズが上限（10 B / ファイル）を超えるため、送信しませんでした。")).toBeVisible();
  await expect(page.getByText("huge.txt")).toBeVisible();
  // 取込ジョブを作らないため「処理待ち / スキップ」ではなく、保存済みと重複の可能性を示す。
  const metrics = page.locator(".tnum");
  await expect(page.getByText("選択したファイル")).toBeVisible();
  await expect(page.getByText("保存済み")).toBeVisible();
  await expect(page.getByText("重複の可能性")).toBeVisible();
  await expect(page.getByText("処理待ち")).toHaveCount(0);
  await expect(metrics.nth(0)).toHaveText("4");
  await expect(metrics.nth(1)).toHaveText("3");
  await expect(metrics.nth(2)).toHaveText("1");
  await expect(metrics.nth(3)).toHaveText("1");
  await expectNoPageOverflow(page);
});

test("途中のまとまりが失敗しても、保存できたファイルの結果と失敗したファイルを示す", async ({ page }) => {
  await mockLocalAuth(page);
  await mockUploadPage(page, { maxUploadBytes: 10 });
  let calls = 0;
  await page.route("**/api/documents/batch-upload", async (route) => {
    calls += 1;
    if (calls === 2) {
      // 前段の proxy が返す 413（ApiResponse の本文を持たない）。
      await route.fulfill({ status: 413, contentType: "text/html", body: "<html>413</html>" });
      return;
    }
    await route.fulfill({
      json: apiEnvelope({
        items: [uploadResult("a.txt")],
        failed_items: [],
        total_count: 1,
        uploaded_count: 1,
        failed_count: 0,
        queued_count: 0,
        skipped_count: 0,
      }),
    });
  });

  await page.goto("/upload");
  await page.locator('input[type="file"]').setInputFiles([textFile("a.txt", 8), textFile("b.txt", 8)]);

  await expect(page.getByRole("heading", { name: "アップロード結果" })).toBeVisible();
  await expect(page.getByTitle("a.txt").first()).toBeVisible();
  await expect(page.getByText("b.txt")).toBeVisible();
  await expect(page.getByText("送信サイズが上限を超えたため、アップロードできませんでした。", { exact: false })).toBeVisible();
});

test("送信中は経過時間を示し、KB 一覧の読み込み中は Skeleton で領域を確保する", async ({ page }) => {
  await mockLocalAuth(page);
  let releaseKnowledgeBases: () => void = () => undefined;
  const knowledgeBasesReleased = new Promise<void>((resolve) => {
    releaseKnowledgeBases = resolve;
  });
  await mockUploadPage(page, {
    knowledgeBases: async (route) => {
      await knowledgeBasesReleased;
      await route.fulfill({
        json: apiEnvelope({ items: [knowledgeBase(1)], total: 1, limit: 200, offset: 0, has_next: false }),
      });
    },
  });
  let releaseUpload: () => void = () => undefined;
  const uploadReleased = new Promise<void>((resolve) => {
    releaseUpload = resolve;
  });
  await page.route("**/api/documents/upload", async (route) => {
    await uploadReleased;
    await route.fulfill({
      status: 503,
      json: { data: null, error_messages: ["保存先に保存できませんでした。"], warning_messages: [] },
    });
  });

  await page.goto("/upload");
  const loading = page.getByTestId("upload-knowledge-base-loading");
  await expect(loading).toBeVisible();
  await expect(loading.getByText("知識ベースを読み込んでいます。").first()).toBeVisible();
  await expect(loading.getByRole("timer")).toBeVisible();
  releaseKnowledgeBases();
  await expect(loading).toHaveCount(0);
  await expect(page.getByRole("combobox", { name: "アップロード先の知識ベース" })).toBeVisible();

  await page.locator('input[type="file"]').setInputFiles(textFile("policy.txt", 6));
  const processing = page.getByTestId("upload-processing");
  await expect(processing).toBeVisible();
  await expect(processing.getByText("1 件のファイルをアップロードしています").first()).toBeVisible();
  await expect(page.getByTestId("upload-processing-timer")).toBeVisible();
  await expectNoPageOverflow(page);
  releaseUpload();
  await expect(processing).toHaveCount(0);
  await expect(page.getByText("保存先に保存できませんでした。")).toBeVisible();
});

test("KB 一覧を取得できないときは、その場で再読み込みできる", async ({ page }) => {
  await mockAuthUser(page, { permissions: ["menu.upload"], allowed_knowledge_base_ids: ["kb-1"] });
  let failing = true;
  await mockUploadPage(page, {
    knowledgeBases: async (route) => {
      if (failing) {
        await route.fulfill({
          status: 500,
          json: { data: null, error_messages: ["一時的なエラー"], warning_messages: [] },
        });
        return;
      }
      await route.fulfill({
        json: apiEnvelope({ items: [knowledgeBase(1)], total: 1, limit: 200, offset: 0, has_next: false }),
      });
    },
  });

  await page.goto("/upload");
  const picker = page.getByTestId("upload-knowledge-base-picker");
  // TanStack Query の既定の再試行（3 回）を終えてから失敗を表示する。
  await expect(picker.getByText("知識ベース一覧を取得できませんでした。")).toBeVisible({ timeout: 15_000 });
  // 必須の利用者がファイルを選んでも黙って何も起きないのではなく、案内を出す。
  await page.locator('input[type="file"]').setInputFiles(textFile("policy.txt", 6));
  await expect(
    picker.getByRole("alert").filter({ hasText: "アップロードする前に、登録先の知識ベースを 1 件以上選択してください。" })
  ).toBeVisible();
  failing = false;
  await picker.getByRole("button", { name: "再読み込み" }).click();
  await expect(page.getByRole("combobox", { name: "アップロード先の知識ベース" })).toBeVisible();
});

test("アップロードだけを許可された利用者には、設定と知識ベース管理への導線を出さない", async ({ page }) => {
  await mockAuthUser(page, { permissions: ["menu.upload"], allowed_knowledge_base_ids: null });
  await mockUploadPage(page, { knowledgeBaseCount: 0 });

  await page.goto("/upload");
  await expect(page.getByText("現在の保存先")).toBeVisible();
  await expect(page.getByText("まだ知識ベースがありません。", { exact: false })).toBeVisible();
  await expect(page.getByRole("link", { name: "保存先設定" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "知識ベース管理" })).toHaveCount(0);
});

test("管理者には保存先設定と知識ベース管理への導線を出す", async ({ page }) => {
  await mockLocalAuth(page);
  await mockUploadPage(page, { knowledgeBaseCount: 0 });

  await page.goto("/upload");
  await expect(page.getByRole("link", { name: "保存先設定" })).toBeVisible();
  await expect(page.getByRole("link", { name: "知識ベース管理" })).toBeVisible();
});

test("先頭ページ（50 件）を超える知識ベースも選べる", async ({ page }) => {
  await mockLocalAuth(page);
  await mockUploadPage(page, { knowledgeBaseCount: 230 });

  await page.goto("/upload");
  const combobox = page.getByRole("combobox", { name: "アップロード先の知識ベース" });
  await combobox.click();
  await combobox.fill("知識ベース 230");
  await expect(
    page.getByRole("listbox", { name: "アップロード先の知識ベース" }).getByRole("option", { name: /知識ベース 230/ })
  ).toBeVisible();
});
