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
// - 設定・ナレッジベース管理を開けない利用者には導線を出さない
// - 先頭ページ（50 件）を超える KB も選べる

function knowledgeBase(index: number) {
  return {
    id: `kb-${index}`,
    name: `ナレッジベース ${String(index).padStart(3, "0")}`,
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
  };
}

async function mockUploadPage(
  page: Page,
  {
    maxUploadBytes = 200 * 1024 * 1024,
    knowledgeBaseCount = 1,
    knowledgeBases,
    ingestionJobs,
  }: {
    maxUploadBytes?: number;
    knowledgeBaseCount?: number;
    knowledgeBases?: (route: Route) => Promise<void>;
    ingestionJobs?: (route: Route) => Promise<void>;
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
        // 201 件以上はサーバー側の検索（q）に切り替わる（#578）。選択済みは ids で引く。
        const q = url.searchParams.get("q")?.trim();
        const ids = url.searchParams.getAll("ids");
        const matched = all.filter(
          (item) => (!q || item.name.includes(q)) && (ids.length === 0 || ids.includes(item.id))
        );
        return route.fulfill({
          json: apiEnvelope({
            items: matched.slice(offset, offset + limit),
            total: matched.length,
            limit,
            offset,
            has_next: offset + limit < matched.length,
          }),
        });
      })
  );
  await page.route(
    "**/api/documents/ingestion-jobs**",
    ingestionJobs ??
      ((route) =>
        route.fulfill({ json: apiEnvelope({ items: [], total: 0, limit: 5, offset: 0, has_next: false }) }))
  );
}

function ingestionJob(id: string, documentFileName: string | null, status = "QUEUED") {
  return {
    id,
    document_id: `doc-${id}`,
    document_file_name: documentFileName,
    recipe_id: null,
    recipe_revision: null,
    status,
    phase: "PREPROCESS",
    parser_profile: "local_text_structure",
    quality_warnings: [],
    skip_reason: null,
    error_message: null,
    attempt_count: 0,
    max_attempts: 3,
    queued_at: "2026-06-16T00:00:00Z",
    started_at: null,
    finished_at: null,
  };
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
  await expect(loading.getByText("ナレッジベースを読み込んでいます。").first()).toBeVisible();
  await expect(loading.getByRole("timer")).toBeVisible();
  releaseKnowledgeBases();
  await expect(loading).toHaveCount(0);
  await expect(page.getByRole("combobox", { name: "所属させるナレッジベース" })).toBeVisible();

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
  await expect(picker.getByText("ナレッジベース一覧を取得できませんでした。")).toBeVisible({ timeout: 15_000 });
  // 必須の利用者がファイルを選んでも黙って何も起きないのではなく、案内を出す。
  await page.locator('input[type="file"]').setInputFiles(textFile("policy.txt", 6));
  await expect(
    picker.getByRole("alert").filter({ hasText: "所属させるナレッジベースを 1 件以上選択してください。" })
  ).toBeVisible();
  failing = false;
  await picker.getByRole("button", { name: "再読み込み" }).click();
  await expect(page.getByRole("combobox", { name: "所属させるナレッジベース" })).toBeVisible();
});

test("アップロードだけを許可された利用者には、設定とナレッジベース管理への導線を出さない", async ({ page }) => {
  await mockAuthUser(page, { permissions: ["menu.upload"], allowed_knowledge_base_ids: null });
  await mockUploadPage(page, { knowledgeBaseCount: 0 });

  await page.goto("/upload");
  await expect(page.getByText("現在の保存先")).toBeVisible();
  await expect(page.getByText("まだナレッジベースがありません。", { exact: false })).toBeVisible();
  await expect(page.getByRole("link", { name: "保存先設定" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "ナレッジベース管理" })).toHaveCount(0);
});

test("管理者には保存先設定とナレッジベース管理への導線を出す", async ({ page }) => {
  await mockLocalAuth(page);
  await mockUploadPage(page, { knowledgeBaseCount: 0 });

  await page.goto("/upload");
  await expect(page.getByRole("link", { name: "保存先設定" })).toBeVisible();
  await expect(page.getByRole("link", { name: "ナレッジベース管理" })).toBeVisible();
});

test("200 件を超えるナレッジベースは、サーバー側で検索して選べる（#578）", async ({ page }) => {
  await mockLocalAuth(page);
  await mockUploadPage(page, { knowledgeBaseCount: 230 });

  await page.goto("/upload");
  const combobox = page.getByRole("combobox", { name: "所属させるナレッジベース" });
  await combobox.click();
  await combobox.fill("ナレッジベース 230");
  await expect(
    page.getByRole("listbox", { name: "所属させるナレッジベース" }).getByRole("option", { name: /ナレッジベース 230/ })
  ).toBeVisible();
});

// 文書アップロードの改善（#306）。
// - 取込ジョブの一覧は文書 ID ではなくファイル名を出す
// - 取込ジョブのパネルの読み込み中は TimedLoadingState + Skeleton、空・取得失敗も同じ枠の中で示す
// - 送信中は件数と経過時間に加えて、送信済み / 合計のバイト数と割合を示す

test("取込ジョブの一覧は、読み込み中は Skeleton で領域を確保し、ファイル名を出す", async ({ page }) => {
  await mockLocalAuth(page);
  let releaseJobs: () => void = () => undefined;
  const jobsReleased = new Promise<void>((resolve) => {
    releaseJobs = resolve;
  });
  await mockUploadPage(page, {
    ingestionJobs: async (route) => {
      await jobsReleased;
      await route.fulfill({
        json: apiEnvelope({
          items: [
            ingestionJob("job-1", "経費精算規程_2026年度版.pdf", "RUNNING"),
            // ファイル名を返さない応答（旧 backend）では文書 ID に戻す。
            ingestionJob("job-2", null, "FAILED"),
          ],
          total: 2,
          limit: 5,
          offset: 0,
          has_next: false,
        }),
      });
    },
  });

  await page.goto("/upload");
  const loading = page.getByTestId("upload-jobs-loading");
  await expect(loading).toBeVisible();
  await expect(loading.getByText("文書処理状況を読み込んでいます").first()).toBeVisible();
  await expect(loading.getByRole("timer")).toBeVisible();
  await expectNoPageOverflow(page);
  releaseJobs();
  await expect(loading).toHaveCount(0);

  const names = page.getByTestId("upload-job-file-name");
  await expect(names).toHaveText(["経費精算規程_2026年度版.pdf", "文書 ID: doc-job-2"]);
  await expect(names.first()).toHaveAttribute("title", "経費精算規程_2026年度版.pdf");
  // ファイル名がある行は文書 ID を出さない。
  await expect(page.getByText(/doc-job-1/)).toHaveCount(0);
  await expectNoPageOverflow(page);
});

test("取込ジョブがないときは空の案内を出し、取得に失敗したときは再試行できる", async ({ page }) => {
  await mockLocalAuth(page);
  let failing = true;
  await mockUploadPage(page, {
    ingestionJobs: async (route) => {
      if (failing) {
        await route.fulfill({
          status: 500,
          json: { data: null, error_messages: ["一時的なエラー"], warning_messages: [] },
        });
        return;
      }
      await route.fulfill({
        json: apiEnvelope({ items: [], total: 0, limit: 5, offset: 0, has_next: false }),
      });
    },
  });

  await page.goto("/upload");
  // TanStack Query の既定の再試行（3 回）を終えてから失敗を表示する。
  await expect(page.getByText("文書処理状況を取得できませんでした。", { exact: false })).toBeVisible({
    timeout: 15_000,
  });
  failing = false;
  await page.getByRole("button", { name: "再試行" }).click();
  await expect(page.getByTestId("upload-jobs-empty")).toHaveText(
    "まだ文書処理はありません。文書を開いて取込を始めると、ここに直近の状況が表示されます。"
  );
  await expectNoPageOverflow(page);
});

/**
 * page.route で応答を差し替えると Chromium は送信の progress event を出さないため、送った XHR を捕まえ、
 * テストから送信済みのバイト数（`xhr.upload` の progress event）を出せるようにする。
 */
async function captureUploadXhr(page: Page) {
  await page.addInitScript(() => {
    const send = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function (body) {
      (window as unknown as { __uploadXhr?: XMLHttpRequest }).__uploadXhr = this;
      return send.call(this, body);
    };
  });
}

async function emitUploadProgress(page: Page, loaded: number, total: number) {
  await page.evaluate(
    ([sent, all]) => {
      const xhr = (window as unknown as { __uploadXhr?: XMLHttpRequest }).__uploadXhr;
      xhr?.upload.dispatchEvent(
        new ProgressEvent("progress", { lengthComputable: true, loaded: sent, total: all })
      );
    },
    [loaded, total]
  );
}

test("送信中は送信済み / 合計のバイト数と割合を示し、送り終えたら保存を待っていることを示す", async ({ page }) => {
  await mockLocalAuth(page);
  await mockUploadPage(page);
  await captureUploadXhr(page);
  let releaseUpload: () => void = () => undefined;
  const uploadReleased = new Promise<void>((resolve) => {
    releaseUpload = resolve;
  });
  let uploadRequests = 0;
  await page.route("**/api/documents/upload", async (route) => {
    uploadRequests += 1;
    await uploadReleased;
    await route.fulfill({
      status: 503,
      json: { data: null, error_messages: ["保存先に保存できませんでした。"], warning_messages: [] },
    });
  });

  await page.goto("/upload");
  const fileBytes = 20 * 1024 * 1024;
  await page.locator('input[type="file"]').setInputFiles(textFile("large.txt", fileBytes));

  const sending = page.getByTestId("upload-sending");
  await expect(sending.getByText("1 件のファイルをアップロードしています").first()).toBeVisible();
  await expect(page.getByTestId("upload-processing-timer")).toBeVisible();
  const bar = sending.getByRole("progressbar", { name: "送信の進み具合" });
  const bytes = page.getByTestId("upload-progress-bytes");
  await expect(bytes).toHaveText("送信済み 0 B / 20 MB（0%）");
  await expect(bar).toHaveAttribute("aria-valuenow", "0");
  await expect.poll(() => uploadRequests).toBe(1);

  // multipart の本文（境界などを含む）の割合を、ファイルのバイト数へ換算して示す。
  const bodyBytes = fileBytes + 400;
  await emitUploadProgress(page, Math.round(bodyBytes * 0.425), bodyBytes);
  await expect(bytes).toHaveText("送信済み 8.5 MB / 20 MB（42%）");
  await expect(bar).toHaveAttribute("aria-valuenow", "42");
  await expect(bar).toHaveAttribute("aria-valuetext", "送信済み 8.5 MB / 20 MB（42%）");
  await expect(page.getByTestId("upload-progress-saving")).toHaveCount(0);
  await expectNoPageOverflow(page);

  // 本文を送り終えると 100%。応答（保存・登録）を待つ間はその旨を示す。
  await emitUploadProgress(page, bodyBytes, bodyBytes);
  await expect(bytes).toHaveText("送信済み 20 MB / 20 MB（100%）");
  await expect(page.getByTestId("upload-progress-saving")).toBeVisible();

  releaseUpload();
  await expect(sending).toHaveCount(0);
  await expect(page.getByText("保存先に保存できませんでした。")).toBeVisible();
});

test("複数のファイルを送るときは、ファイルごとの送信済みの量と状態を示す", async ({ page }) => {
  await mockLocalAuth(page);
  await mockUploadPage(page);
  await captureUploadXhr(page);
  let releaseUpload: () => void = () => undefined;
  const uploadReleased = new Promise<void>((resolve) => {
    releaseUpload = resolve;
  });
  await page.route("**/api/documents/batch-upload", async (route) => {
    await uploadReleased;
    await route.fulfill({
      json: apiEnvelope({
        items: [uploadResult("a.txt"), uploadResult("b.txt"), uploadResult("c.txt")],
        failed_items: [],
        total_count: 3,
        uploaded_count: 3,
        failed_count: 0,
      }),
    });
  });

  await page.goto("/upload");
  const MB = 1024 * 1024;
  // 長いファイル名は省略し、title に全文を出す（375px でも横にはみ出さない）。
  const longName = `${"年度別経費精算規程と出張旅費の取り扱い".repeat(4)}.txt`;
  await page
    .locator('input[type="file"]')
    .setInputFiles([textFile(longName, 6 * MB), textFile("b.txt", 2 * MB), textFile("c.txt", 4 * MB)]);

  const list = page.getByRole("list", { name: "ファイルごとの送信状況" });
  const items = list.getByTestId("upload-file-progress-item");
  await expect(items).toHaveCount(3);
  await expect(items.nth(0)).toHaveAttribute("data-state", "waiting");
  await expect(items.nth(0)).toContainText("待機中 · 0 B / 6 MB");
  await expect(items.nth(0).getByTitle(longName)).toBeVisible();

  // 本文の 7/12 を送った時点: 先頭（6 MB）は送信済み、2 件目は途中、3 件目は待機中。
  const bodyBytes = 12 * MB + 1200;
  await emitUploadProgress(page, (bodyBytes / 12) * 7, bodyBytes);
  await expect(page.getByTestId("upload-progress-bytes")).toHaveText("送信済み 7 MB / 12 MB（58%）");
  await expect(items.nth(0)).toHaveAttribute("data-state", "sent");
  await expect(items.nth(0)).toContainText("送信済み · 6 MB / 6 MB");
  await expect(items.nth(1)).toHaveAttribute("data-state", "sending");
  await expect(items.nth(1)).toContainText("送信中 · 1 MB / 2 MB");
  await expect(items.nth(2)).toHaveAttribute("data-state", "waiting");
  await expect(items.nth(2)).toContainText("待機中 · 0 B / 4 MB");
  // 回るスピナーは送信全体の表示の 1 つだけ。送信中のファイルは静止したアイコンと文言で示す（#416）。
  await expect(page.locator("svg.animate-spin:visible")).toHaveCount(1);
  await expect(page.getByTestId("upload-processing").locator("svg.animate-spin")).toHaveCount(1);
  await expectNoPageOverflow(page);

  releaseUpload();
  await expect(page.getByTestId("upload-sending")).toHaveCount(0);
});

// 既定の文書解析エンジン Docling は PDF と画像だけを解析する（#286）。それ以外の形式は、
// アップロードの結果で「処理レシピで Unstructured を選ぶ」ように案内する（取込は始めない）。
// desktop / mobile（375px）は playwright.config.ts の project で両方実行する。
test("Docling で解析できない形式は、アップロードの結果で案内する", async ({ page }) => {
  await mockLocalAuth(page);
  await mockUploadPage(page);
  const message =
    "文書解析エンジン Docling（既定の解析エンジン）はこのファイル形式（.txt）を解析できないため、取込を開始しませんでした。" +
    "この文書の「処理レシピ」で「文書解析」を Unstructured に変えてから「処理を開始」してください。";
  await page.route("**/api/documents/batch-upload", async (route) => {
    await route.fulfill({
      json: apiEnvelope({
        items: [
          {
            ...uploadResult("memo.txt"),
            parser_notice: {
              code: "parser_source_unsupported",
              backend: "docling",
              file_format: ".txt",
              suggested_backend: "unstructured",
              message,
            },
          },
          uploadResult("policy.txt"),
        ],
        failed_items: [],
        total_count: 2,
        uploaded_count: 2,
        failed_count: 0,
        queued_count: 0,
        skipped_count: 0,
      }),
    });
  });

  await page.goto("/upload");
  await page.locator('input[type="file"]').setInputFiles([textFile("memo.txt", 6), textFile("policy.txt", 6)]);

  await expect(page.getByRole("heading", { name: "アップロード結果" })).toBeVisible();
  // 一覧の行に短い案内、選択中の文書に理由と対処を出す。
  await expect(
    page.getByText("既定の Docling では解析できない形式です（処理レシピで Unstructured を選択）")
  ).toHaveCount(1);
  await expect(page.getByText("このままでは取込を開始できません")).toBeVisible();
  await expect(page.getByTestId("upload-parser-notice")).toHaveText(message);
  await expectNoPageOverflow(page);
  // 案内の無い文書を選ぶと消える。
  await page.getByRole("button", { name: "policy.txt を表示" }).click();
  await expect(page.getByTestId("upload-parser-notice")).toHaveCount(0);
});
