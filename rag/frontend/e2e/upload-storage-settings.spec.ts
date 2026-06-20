import { expect, type Page, test } from "@playwright/test";
import { mockDatabaseReady } from "./_helpers";

interface UploadStorageSettingsData {
  backend: "local" | "oci";
  local_storage_dir: string;
  object_storage_region: string;
  object_storage_namespace: string;
  object_storage_bucket: string;
  readiness: string;
  max_upload_bytes: number;
  config_source: "runtime";
}

const localStorageSettings: UploadStorageSettingsData = {
  backend: "local",
  local_storage_dir: "/u01/production-ready-rag",
  object_storage_region: "ap-osaka-1",
  object_storage_namespace: "",
  object_storage_bucket: "",
  readiness: "ok",
  max_upload_bytes: 200 * 1024 * 1024,
  config_source: "runtime",
};

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await page.route("**/api/auth/me", async (route) => {
    await route.fulfill({
      json: {
        data: {
          mode: "local",
          auth_required: false,
          authenticated: true,
          user: null,
          expires_at: null,
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
});

test("アップロード保存先設定で OCI Object Storage に切り替えられる", async ({
  page,
}) => {
  let current = { ...localStorageSettings };
  let lastPayload: unknown = null;
  await page.addInitScript(() => {
    window.localStorage.setItem(
      "production-ready-rag.oci-settings.v1",
      JSON.stringify({
        objectStorageNamespace: "oci-page-namespace",
      })
    );
  });
  await mockUploadStorageSettings(page, () => current, async (payload) => {
    lastPayload = payload;
    current = {
      ...current,
      ...(payload as Partial<UploadStorageSettingsData>),
      readiness: "ok",
    };
  });

  await page.goto("/settings/upload-storage");

  await expect(
    page.getByRole("heading", { name: "アップロード保存先" })
  ).toBeVisible();
  await expect(page.getByText("200.0 MB")).toBeVisible();
  await expect(page.getByRole("button", { name: ".env をコピー" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "JSON プレビュー" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "運用メモ" })).toBeVisible();
  const envPreview = page.getByLabel(".env プレビュー");
  await expect(envPreview).toContainText("UPLOAD_STORAGE_BACKEND=local");
  await expect(envPreview).toContainText("LOCAL_STORAGE_DIR=/u01/production-ready-rag");
  await page.getByRole("radio", { name: /OCI Object Storage/ }).check();
  await expect(page.getByLabel("Object Storage ネームスペース")).toHaveCount(0);
  await page.getByLabel("Object Storage バケット").fill("rag-originals");
  await expect(envPreview).toContainText("UPLOAD_STORAGE_BACKEND=oci");
  await expect(envPreview).toContainText("OBJECT_STORAGE_REGION=ap-osaka-1");
  await expect(envPreview).toContainText(
    "OBJECT_STORAGE_NAMESPACE=oci-page-namespace"
  );
  await expect(envPreview).toContainText("OBJECT_STORAGE_BUCKET=rag-originals");
  await page.getByRole("button", { name: "保存" }).click();

  await expect(page.getByText("保存しました")).toBeVisible();
  await expect(page.getByText("oci-page-namespace/rag-originals")).toBeVisible();
  expect(lastPayload).toMatchObject({
    backend: "oci",
    object_storage_namespace: "oci-page-namespace",
    object_storage_bucket: "rag-originals",
  });
});

test("アップロード保存先は OCI の未設定項目があっても保存できる", async ({ page }) => {
  let current = { ...localStorageSettings };
  let lastPayload: unknown = null;
  await mockUploadStorageSettings(page, () => current, async (payload) => {
    lastPayload = payload;
    current = {
      ...current,
      ...(payload as Partial<UploadStorageSettingsData>),
      readiness: "missing",
    };
  });

  await page.goto("/settings/upload-storage");

  await page.getByRole("radio", { name: /OCI Object Storage/ }).check();
  const memo = operationMemoCard(page);
  await expect(memo.getByText("値を入力してください。")).toBeVisible();
  await page.getByRole("button", { name: "保存" }).click();

  await expect(page.getByText("保存しました")).toBeVisible();
  await expect(page.getByText("Readiness: 未設定")).toBeVisible();
  expect(lastPayload).toMatchObject({
    backend: "oci",
    object_storage_namespace: "",
    object_storage_bucket: "",
  });
});

test("アップロード画面から現在の保存先と設定導線を確認できる", async ({ page }) => {
  await mockUploadStorageSettings(page, () => ({
    ...localStorageSettings,
    backend: "oci",
    object_storage_namespace: "example-namespace",
    object_storage_bucket: "rag-originals",
  }));
  await mockKnowledgeBases(page);
  await mockIngestionJobs(page);

  await page.goto("/upload");

  await expect(page.getByText("現在の保存先")).toBeVisible();
  await expect(page.getByRole("heading", { name: "所属させる知識ベース" })).toBeVisible();
  await expect(page.getByText("PDF・画像・テキスト・HTML・メール・Office")).toBeVisible();
  await expect(page.getByText("最大 200 MB")).toBeVisible();
  await expect(page.getByText("音声は保存のみで取込はスキップされます。")).toBeVisible();
  const acceptedTypes = await page.locator('input[type="file"]').getAttribute("accept");
  expect(acceptedTypes).toContain(".docx");
  expect(acceptedTypes).toContain(".pptx");
  expect(acceptedTypes).toContain(".xlsx");
  expect(acceptedTypes).toContain(".html");
  expect(acceptedTypes).toContain(".eml");
  expect(acceptedTypes).toContain("audio/mpeg");
  await expect(page.getByText("example-namespace/rag-originals")).toBeVisible();
  await page.getByRole("link", { name: "保存先設定" }).click();
  await expect(page).toHaveURL(/\/settings\/upload-storage$/);
});

test("アップロード時に選択した知識ベースへ所属できる", async ({ page }) => {
  let uploadBody = "";
  await mockUploadStorageSettings(page, () => localStorageSettings);
  await mockKnowledgeBases(page);
  await mockIngestionJobs(page);
  await mockDocumentUpload(page, (body) => {
    uploadBody = body;
  });

  await page.goto("/upload");

  const kbCombo = page.getByRole("combobox", { name: "アップロード先の知識ベース" });
  await kbCombo.click();
  await page.getByRole("option", { name: /社内規程/ }).click();
  await kbCombo.press("Escape");
  await expect(page.getByText("1 件の知識ベースへ登録します。")).toBeVisible();
  await page.locator('input[type="file"]').setInputFiles({
    name: "upload.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("本文"),
  });

  await expect(page.getByRole("heading", { name: "upload.txt" })).toBeVisible();
  expect(uploadBody).toContain('name="knowledge_base_ids"');
  expect(uploadBody).toContain("kb-1");
});

test("アップロード画面で自動取込と原本プロファイルを確認できる", async ({ page }) => {
  await mockUploadStorageSettings(page, () => localStorageSettings);
  await mockKnowledgeBases(page);
  await mockIngestionJobs(page, [ingestionJob("job-upload", "doc-upload", "SUCCEEDED")]);
  await mockDocumentUpload(page, () => undefined);

  await page.goto("/upload");

  await page.getByLabel("アップロード後に自動取込").check();
  await expect(page.getByText("OCR・構造化抽出")).toBeVisible();
  await page.locator('input[type="file"]').setInputFiles({
    name: "upload.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("本文"),
  });

  await expect(page.getByText("自動取込を開始しています")).toBeVisible();
  await expect(page.getByRole("heading", { name: "原本プロファイル" })).toBeVisible();
  await expect(page.getByText("テキスト構造化")).toBeVisible();
  await expect(page.getByText("text/plain")).toBeVisible();
});

test("未対応 audio は自動取込を開始せずスキップとして表示する", async ({ page }) => {
  await mockUploadStorageSettings(page, () => localStorageSettings);
  await mockKnowledgeBases(page);
  await mockIngestionJobs(page, [ingestionJob("job-audio", "doc-audio", "SKIPPED")]);
  await mockDocumentUpload(page, () => undefined, { kind: "audio" });

  await page.goto("/upload");

  await page.getByLabel("アップロード後に自動取込").check();
  await page.locator('input[type="file"]').setInputFiles({
    name: "voice.mp3",
    mimeType: "audio/mpeg",
    buffer: Buffer.from("ID3"),
  });

  await expect(page.getByRole("heading", { name: "voice.mp3" })).toBeVisible();
  await expect(page.getByText("スキップ")).toBeVisible();
  await expect(page.getByText("音声", { exact: true })).toBeVisible();
  await expect(page.getByText("音声未対応", { exact: true })).toBeVisible();
  await expect(page.getByText("音声ファイルは現在の取込対象外です。").first()).toBeVisible();
  await expect(page.getByText("自動取込を開始しています")).toHaveCount(0);
});

test("アップロード画面から取込ジョブを再開・再試行できる", async ({ page }) => {
  let drained = false;
  let retriedJobId = "";
  await mockUploadStorageSettings(page, () => localStorageSettings);
  await mockKnowledgeBases(page);
  await mockIngestionJobs(
    page,
    [
      ingestionJob("job-queued", "doc-queued", "QUEUED"),
      {
        ...ingestionJob("job-failed", "doc-failed", "FAILED"),
        error_message: "前回の取込に失敗しました。",
      },
    ],
    {
      onDrain: () => {
        drained = true;
      },
      onRetry: (jobId) => {
        retriedJobId = jobId;
      },
    }
  );

  await page.goto("/upload");

  await expect(page.getByRole("heading", { name: "取込ジョブ" })).toBeVisible();
  await expect(page.getByText("前回の取込に失敗しました。")).toBeVisible();
  await page.getByRole("button", { name: "待機ジョブを再開" }).click();
  await expect.poll(() => drained).toBe(true);
  await page.getByRole("button", { name: "再試行" }).click();
  await expect.poll(() => retriedJobId).toBe("job-failed");
});

test("複数ファイルをまとめてアップロードし取込ジョブを確認できる", async ({ page }) => {
  let uploadBody = "";
  await mockUploadStorageSettings(page, () => localStorageSettings);
  await mockKnowledgeBases(page);
  await mockIngestionJobs(page, [
    ingestionJob("job-batch-1", "doc-batch-1", "SUCCEEDED"),
    ingestionJob("job-batch-2", "doc-batch-2", "SUCCEEDED"),
  ]);
  await mockBatchDocumentUpload(page, (body) => {
    uploadBody = body;
  });

  await page.goto("/upload");

  await page.getByLabel("アップロード後に自動取込").check();
  await page.locator('input[type="file"]').setInputFiles([
    {
      name: "policy-a.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("A 本文"),
    },
    {
      name: "policy-b.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("B 本文"),
    },
    {
      name: "policy.exe",
      mimeType: "application/x-msdownload",
      buffer: Buffer.from("MZ"),
    },
  ]);

  await expect(page.getByRole("heading", { name: "アップロード結果" })).toBeVisible();
  await expect(page.getByTitle("policy-a.txt").first()).toBeVisible();
  await expect(page.getByTitle("policy-b.txt").first()).toBeVisible();
  await expect(page.getByText("一部のファイルをアップロードできませんでした")).toBeVisible();
  await expect(page.getByText("policy.exe")).toBeVisible();
  await expect(page.getByText("汎用解析")).toBeVisible();
  await expect(page.getByText("未対応")).toBeVisible();
  await expect(page.getByText("原本種別を自動判定できませんでした。")).toBeVisible();
  await expect(page.getByText("待機中")).toHaveCount(2);
  await page.getByRole("button", { name: "policy-b.txt を表示" }).click();
  await expect(page.getByRole("heading", { name: "policy-b.txt" })).toBeVisible();
  expect(uploadBody).toContain('name="files"');
  expect(uploadBody).toContain("policy-a.txt");
  expect(uploadBody).toContain("policy-b.txt");
  expect(uploadBody).toContain("policy.exe");
});

async function mockUploadStorageSettings(
  page: Page,
  getCurrent: () => UploadStorageSettingsData,
  onPatch?: (payload: unknown) => Promise<void>
) {
  await page.route("**/api/settings/upload-storage", async (route) => {
    const request = route.request();
    if (request.method() === "PATCH") {
      const payload = request.postDataJSON();
      await onPatch?.(payload);
    }

    await route.fulfill({
      json: {
        data: getCurrent(),
        error_messages: [],
        warning_messages: [],
      },
    });
  });
}

async function mockKnowledgeBases(page: Page) {
  await page.route("**/api/knowledge-bases**", async (route) => {
    await route.fulfill({
      json: {
        data: {
          items: [
            {
              id: "kb-1",
              name: "社内規程",
              description: "検索対象の規程",
              status: "ACTIVE",
              default_search_mode: "hybrid",
              document_count: 3,
              indexed_document_count: 3,
              error_document_count: 0,
              searchable_chunk_count: 12,
              created_at: "2026-06-15T00:00:00Z",
              updated_at: "2026-06-15T00:00:00Z",
              archived_at: null,
            },
          ],
          total: 1,
          limit: 50,
          offset: 0,
          has_next: false,
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
}

async function mockDocumentUpload(
  page: Page,
  onUpload: (body: string) => void,
  options: { kind?: "text" | "audio" } = {}
) {
  await page.route("**/api/documents/upload", async (route) => {
    const body = route.request().postData() ?? "";
    onUpload(body);
    const autoIngest = body.includes("ingestion_mode") && body.includes("auto");
    const audio = options.kind === "audio";
    const documentId = audio ? "doc-audio" : "doc-upload";
    const fileName = audio ? "voice.mp3" : "upload.txt";
    await route.fulfill({
      json: {
        data: {
          id: documentId,
          file_name: fileName,
          status: "UPLOADED",
          file_size_bytes: audio ? 3 : 6,
          content_sha256: audio ? "a".repeat(64) : "b".repeat(64),
          duplicate_of_document_id: null,
          knowledge_bases: [{ id: "kb-1", name: "社内規程" }],
          source_profile: audio ? audioUploadSourceProfile() : uploadSourceProfile(),
          ingestion_started: autoIngest && !audio,
          ingestion_job: autoIngest
            ? ingestionJob(
                audio ? "job-audio" : "job-upload",
                documentId,
                audio ? "SKIPPED" : "QUEUED"
              )
            : null,
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  if (options.kind === "audio") {
    await mockDocumentDetail(page, "doc-audio", "voice.mp3", audioUploadSourceProfile());
    return;
  }
  await mockDocumentDetail(page, "doc-upload", "upload.txt");
}

async function mockBatchDocumentUpload(page: Page, onUpload: (body: string) => void) {
  await page.route("**/api/documents/batch-upload", async (route) => {
    const body = route.request().postData() ?? "";
    onUpload(body);
    await route.fulfill({
      json: {
        data: {
          items: [
            uploadResult("doc-batch-1", "policy-a.txt", ingestionJob("job-batch-1", "doc-batch-1")),
            uploadResult("doc-batch-2", "policy-b.txt", ingestionJob("job-batch-2", "doc-batch-2")),
          ],
          failed_items: [
            {
              file_name: "policy.exe",
              status_code: 415,
              message: "対応していないファイル形式です。",
              source_profile: failedUploadSourceProfile(),
            },
          ],
          total_count: 3,
          uploaded_count: 2,
          failed_count: 1,
          queued_count: 2,
          skipped_count: 0,
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await mockDocumentDetail(page, "doc-batch-1", "policy-a.txt");
  await mockDocumentDetail(page, "doc-batch-2", "policy-b.txt");
}

async function mockDocumentDetail(
  page: Page,
  documentId: string,
  fileName: string,
  sourceProfile = uploadSourceProfile(fileName)
) {
  await page.route(`**/api/documents/${documentId}`, async (route) => {
    await route.fulfill({
      json: {
        data: {
          id: documentId,
          file_name: fileName,
          status: "UPLOADED",
          category_name: null,
          content_type: sourceProfile.content_type,
          file_size_bytes: sourceProfile.file_size_bytes,
          content_sha256: sourceProfile.content_sha256,
          duplicate_of_document_id: null,
          uploaded_at: "2026-06-15T00:00:00Z",
          indexed_at: null,
          object_storage_path: "local://upload.txt",
          extraction: {
            raw_text: "本文",
            document_type: "テキスト",
            confidence: 0.98,
            warnings: [],
            elements: [],
          },
          error_message: null,
          knowledge_bases: [{ id: "kb-1", name: "社内規程" }],
          source_profile: sourceProfile,
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await page.route(`**/api/documents/${documentId}/content`, async (route) => {
    await route.fulfill({
      status: 200,
      headers: { "content-type": "text/plain" },
      body: "本文",
    });
  });
  await page.route(`**/api/documents/${documentId}/knowledge-bases`, async (route) => {
    await route.fulfill({
      json: {
        data: [{ id: "kb-1", name: "社内規程" }],
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await page.route(`**/api/documents/${documentId}/chunks`, async (route) => {
    await route.fulfill({
      json: {
        data: [],
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await page.route(`**/api/documents/${documentId}/ingestion-segments`, async (route) => {
    await route.fulfill({
      json: {
        data: [
          {
            segment_id: `${documentId}:source`,
            document_id: documentId,
            status: "UPLOADED",
            parser_backend: "local_partition",
            parser_profile: "local_text_structure",
            page_start: null,
            page_end: null,
            attempt_count: 0,
            artifact_path: "local://upload.txt",
            error_code: null,
            error_message: null,
          },
        ],
        error_messages: [],
        warning_messages: [],
      },
    });
  });
}

async function mockIngestionJobs(
  page: Page,
  jobs: ReturnType<typeof ingestionJob>[] = [],
  options: {
    onDrain?: () => void;
    onRetry?: (jobId: string) => void;
  } = {}
) {
  await page.route("**/api/documents/ingestion-jobs**", async (route) => {
    const request = route.request();
    const url = request.url();
    if (request.method() === "POST" && url.includes("/drain")) {
      options.onDrain?.();
      await route.fulfill({
        json: {
          data: jobs.filter((job) => job.status === "QUEUED"),
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }
    if (request.method() === "POST" && url.includes("/retry")) {
      const match = url.match(/\/ingestion-jobs\/([^/]+)\/retry/);
      options.onRetry?.(decodeURIComponent(match?.[1] ?? ""));
      await route.fulfill({
        json: {
          data: ingestionJob("job-retry", "doc-failed", "QUEUED"),
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }
    await route.fulfill({
      json: {
        data: {
          items: jobs,
          total: jobs.length,
          limit: 5,
          offset: 0,
          has_next: false,
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
}

function uploadResult(id: string, fileName: string, job: ReturnType<typeof ingestionJob>) {
  return {
    id,
    file_name: fileName,
    status: "UPLOADED",
    file_size_bytes: 6,
    content_sha256: "b".repeat(64),
    duplicate_of_document_id: null,
    knowledge_bases: [{ id: "kb-1", name: "社内規程" }],
    source_profile: uploadSourceProfile(fileName),
    ingestion_started: true,
    ingestion_job: job,
  };
}

function ingestionJob(
  id: string,
  documentId: string,
  status: "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "SKIPPED" = "QUEUED"
) {
  const audio = documentId === "doc-audio";
  return {
    id,
    document_id: documentId,
    status,
    parser_profile: audio ? "unsupported_audio" : "local_text_structure",
    quality_warnings: audio ? ["unsupported_audio"] : [],
    skip_reason:
      audio && status === "SKIPPED" ? "audio_transcription_not_configured" : null,
    error_message: null as string | null,
    attempt_count: status === "QUEUED" ? 0 : 1,
    max_attempts: 3,
    queued_at: "2026-06-15T00:00:00Z",
    started_at: status === "QUEUED" || status === "SKIPPED" ? null : "2026-06-15T00:00:01Z",
    finished_at:
      status === "QUEUED" || status === "RUNNING" ? null : "2026-06-15T00:00:03Z",
  };
}

function uploadSourceProfile(fileName = "upload.txt") {
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

function audioUploadSourceProfile() {
  return {
    original_file_name: "voice.mp3",
    sanitized_file_name: "voice.mp3",
    extension: ".mp3",
    content_type: "audio/mpeg",
    inferred_content_type: "audio/mpeg",
    file_size_bytes: 3,
    content_sha256: "a".repeat(64),
    modality: "audio",
    parser_profile: "unsupported_audio",
    parser_backend: "unsupported",
    parser_version: "v1",
    preview_kind: "unsupported",
    text_charset: null,
    duplicate_of_document_id: null,
    unsupported_reason: "audio_transcription_not_configured",
    quality_status: "warning",
    quality_warnings: ["unsupported_audio"],
  };
}

function failedUploadSourceProfile() {
  return {
    original_file_name: "policy.exe",
    sanitized_file_name: "policy.exe",
    extension: ".exe",
    content_type: "application/x-msdownload",
    inferred_content_type: "application/x-msdownload",
    file_size_bytes: 2,
    content_sha256: "c".repeat(64),
    modality: "unknown",
    parser_profile: "enterprise_ai_generic",
    parser_backend: "enterprise_ai",
    parser_version: "v1",
    preview_kind: "unsupported",
    text_charset: null,
    duplicate_of_document_id: null,
    unsupported_reason: "unknown_file_type",
    quality_status: "warning",
    quality_warnings: ["unknown_modality"],
  };
}

function operationMemoCard(page: Page) {
  return page
    .getByRole("heading", { name: "運用メモ" })
    .locator(
      "xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' rounded-lg ')][1]"
    );
}
