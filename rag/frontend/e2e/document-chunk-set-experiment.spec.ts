import { expect, type Page, test } from "@playwright/test";

import { expectNoPageOverflow, mockDatabaseReady } from "./_helpers";

// 文書詳細「別レシピを試す」実験パネル: 候補 materialize → 横並び比較 → 昇格 を検証する。
// backend(Phase 3a)は実 API、ここでは route mock で UI フローを固定する。

const authStatus = {
  data: { mode: "local", auth_required: false, authenticated: true, user: null, expires_at: null },
  error_messages: [],
  warning_messages: [],
};

const DOC_ID = "doc-experiment";
const SERVING_ID = "cs_serving_000000000";
const CANDIDATE_ID = "cs_candidate_00000000";
const REPARSE_JOB_ID = "job-reparse-0001";

function indexedDetail() {
  return {
    id: DOC_ID,
    file_name: "policy.txt",
    status: "INDEXED",
    category_name: null,
    content_type: "text/plain",
    file_size_bytes: 64,
    content_sha256: "a".repeat(64),
    duplicate_of_document_id: null,
    uploaded_at: "2026-06-29T00:00:00Z",
    indexed_at: "2026-06-29T00:01:00Z",
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
      tables: [],
      assets: [],
      fields: [],
      quality_report: null,
      duplicate_of_document_id: null,
      unsupported_reason: null,
      quality_status: "ready",
      quality_warnings: [],
    },
    knowledge_bases: [{ id: "kb-1", name: "社内規程" }],
  };
}

function chunkSet(id: string, isServing: boolean, chunkCount: number) {
  return {
    chunk_set_id: id,
    extraction_recipe_id: "er_1",
    extraction_status: "materialized",
    extraction_reason: null,
    status: "INDEXED",
    chunk_count: chunkCount,
    vector_count: chunkCount,
    is_serving: isServing,
    extraction_id: null,
    parser: null,
    preprocess: null,
    knowledge_base_ids: ["kb-1"],
    serving_knowledge_base_ids: isServing ? ["kb-1"] : [],
    layer_statuses: {
      metadata: { layer_id: null, requested: false, status: "not_requested", reason: null },
      graph: { layer_id: null, requested: false, status: "not_requested", reason: null },
      navigation: { layer_id: null, requested: false, status: "not_requested", reason: null },
    },
  };
}

function reparseJob(status: string) {
  return {
    id: REPARSE_JOB_ID,
    document_id: DOC_ID,
    status,
    phase: "EXTRACT",
    parser_profile: "local_text_structure",
    quality_warnings: [],
    skip_reason: null,
    error_message: null,
    attempt_count: 1,
    max_attempts: 3,
    queued_at: "2026-06-29T00:02:00Z",
    started_at: status === "QUEUED" ? null : "2026-06-29T00:02:01Z",
    finished_at: status === "SUCCEEDED" ? "2026-06-29T00:02:05Z" : null,
  };
}

function citation(chunkId: string, text: string) {
  return {
    document_id: DOC_ID,
    chunk_id: chunkId,
    text,
    score: 0.9,
    rerank_score: 0.8,
    file_name: "policy.txt",
    category_name: null,
    metadata: {},
  };
}

async function mockExperimentWorkspace(page: Page) {
  const state = { created: false, promoted: false };
  const calls = {
    create: 0,
    promote: 0,
    search: 0,
    reparse: 0,
    lastCreateBody: null as unknown,
    lastReparseBody: null as unknown,
  };

  await mockDatabaseReady(page);
  await page.route("**/api/auth/me", (route) => route.fulfill({ json: authStatus }));
  await page.route("**/api/knowledge-bases**", (route) =>
    route.fulfill({
      json: {
        data: {
          items: [{ id: "kb-1", name: "社内規程", document_count: 1 }],
          total: 1,
          limit: 100,
          offset: 0,
          has_next: false,
        },
        error_messages: [],
        warning_messages: [],
      },
    })
  );

  // chunk-sets: 初期=serving のみ。候補作成後=serving+candidate。昇格後=candidate のみ。
  await page.route(`**/api/documents/${DOC_ID}/chunk-sets`, (route) => {
    let data: ReturnType<typeof chunkSet>[];
    if (state.promoted) {
      data = [chunkSet(CANDIDATE_ID, true, 9)];
    } else if (state.created) {
      data = [chunkSet(SERVING_ID, true, 5), chunkSet(CANDIDATE_ID, false, 9)];
    } else {
      data = [chunkSet(SERVING_ID, true, 5)];
    }
    route.fulfill({ json: { data, error_messages: [], warning_messages: [] } });
  });

  await page.route(`**/api/documents/${DOC_ID}/chunk-set-experiments`, async (route) => {
    calls.create += 1;
    const body = route.request().postData();
    calls.lastCreateBody = body ? JSON.parse(body) : null;
    state.created = true;
    await route.fulfill({
      json: { data: chunkSet(CANDIDATE_ID, false, 9), error_messages: [], warning_messages: [] },
    });
  });

  await page.route(
    `**/api/documents/${DOC_ID}/chunk-set-experiments/${CANDIDATE_ID}/promote`,
    async (route) => {
      calls.promote += 1;
      state.promoted = true;
      await route.fulfill({
        json: { data: chunkSet(CANDIDATE_ID, true, 9), error_messages: [], warning_messages: [] },
      });
    }
  );

  // parser/前処理 再抽出: 投入で候補を materialize 予約、ジョブ状態は polling で SUCCEEDED を返す。
  await page.route(`**/api/documents/${DOC_ID}/parser-extraction-experiments`, async (route) => {
    calls.reparse += 1;
    const body = route.request().postData();
    calls.lastReparseBody = body ? JSON.parse(body) : null;
    state.created = true;
    await route.fulfill({
      json: { data: reparseJob("QUEUED"), error_messages: [], warning_messages: [] },
    });
  });
  await page.route(`**/api/documents/ingestion-jobs/${REPARSE_JOB_ID}`, (route) =>
    route.fulfill({
      json: { data: reparseJob("SUCCEEDED"), error_messages: [], warning_messages: [] },
    })
  );

  await page.route("**/api/search", async (route) => {
    calls.search += 1;
    await route.fulfill({
      json: {
        data: {
          answer: "",
          citations: [citation("ck-1", "交通費は1000円です。"), citation("ck-2", "経費申請")],
          guardrail_warnings: [],
          trace_id: "trace-1",
          elapsed_ms: 12,
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });

  // DocumentWorkspace が参照するその他の付随 route(空で十分)。
  for (const path of [
    "chunks",
    "ingestion-segments",
    "knowledge-bases",
  ]) {
    await page.route(`**/api/documents/${DOC_ID}/${path}`, (route) =>
      route.fulfill({ json: { data: [], error_messages: [], warning_messages: [] } })
    );
  }
  await page.route(`**/api/documents/${DOC_ID}/ingestion-jobs**`, (route) =>
    route.fulfill({ json: { data: [], error_messages: [], warning_messages: [] } })
  );
  await page.route(`**/api/documents/${DOC_ID}/ingestion-config`, (route) =>
    route.fulfill({
      json: {
        data: {
          document_id: DOC_ID,
          is_indexed: true,
          owning_knowledge_base: { id: "kb-1", name: "社内規程" },
          effective_chunking_strategy: "structure_aware",
          effective_parser_adapter_backend: "local_text_structure",
          observed_chunking_strategy: "structure_aware",
          observed_parser_backend: "local_text_structure",
          chunking_drift: false,
          parser_drift: false,
          config_drift: false,
        },
        error_messages: [],
        warning_messages: [],
      },
    })
  );
  await page.route(`**/api/documents/${DOC_ID}/extraction-export**`, (route) =>
    route.fulfill({
      json: {
        data: {
          document_id: DOC_ID,
          file_name: "policy.txt",
          format: "markdown",
          content_type: "text/markdown; charset=utf-8",
          content: "# 経費申請",
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
    route.fulfill({ status: 200, contentType: "text/plain", body: "経費申請" })
  );
  await page.route(`**/api/documents/${DOC_ID}`, (route) =>
    route.fulfill({ json: { data: indexedDetail(), error_messages: [], warning_messages: [] } })
  );

  return calls;
}

test("候補を作成 → 横並び比較 → 昇格 まで通せる", async ({ page }) => {
  const calls = await mockExperimentWorkspace(page);
  await page.goto(`/documents/${DOC_ID}`);

  // 配信中バッジが見える。
  await expect(page.getByText("別レシピを試す").first()).toBeVisible();
  await expect(page.getByText("配信中").first()).toBeVisible();

  // 候補を作成(チャンク長を指定)。
  await page.locator("#experiment-chunk-size").fill("400");
  await page.getByRole("button", { name: "候補を作成", exact: true }).click();
  await expect(page.getByText("候補のチャンク構成を作成しました。")).toBeVisible();
  expect(calls.create).toBe(1);
  expect(calls.lastCreateBody).toEqual({ chunk_size: 400 });
  await expect(page.getByText("候補").first()).toBeVisible();

  // 横並び比較。
  await page.locator("#experiment-probe-query").fill("交通費");
  await page.getByRole("button", { name: "比較", exact: true }).click();
  await expect(page.getByText("配信中（2）")).toBeVisible();
  await expect(page.getByText("候補（2）")).toBeVisible();
  expect(calls.search).toBe(2);

  // 昇格(確認ダイアログ → 確定)。
  await page.getByRole("button", { name: "昇格", exact: true }).click();
  await page.getByRole("button", { name: "昇格する" }).click();
  await expect(page.getByText("候補を配信に昇格しました。")).toBeVisible();
  expect(calls.promote).toBe(1);
});

test("parser を変えて再抽出 → ジョブ完了で候補が追加される", async ({ page }) => {
  const calls = await mockExperimentWorkspace(page);
  await page.goto(`/documents/${DOC_ID}`);

  await expect(page.getByText("解析・ファイル準備を変えて試す(再抽出)")).toBeVisible();

  // 文書解析を Docling に変更して再抽出ジョブを投入。
  await page.getByRole("combobox", { name: "文書解析" }).click();
  await page.getByRole("option", { name: "Docling" }).click();
  await page.getByRole("button", { name: "再抽出して候補を作成" }).click();

  await expect(
    page.getByText("再抽出ジョブを投入しました。完了すると候補が追加されます。")
  ).toBeVisible();
  expect(calls.reparse).toBe(1);
  expect(calls.lastReparseBody).toEqual({ parser_adapter_backend: "docling" });

  // ジョブ polling が SUCCEEDED → 候補が一覧に並ぶ。
  await expect(page.getByText("再抽出した候補を追加しました。")).toBeVisible();
  await expect(page.getByText("候補").first()).toBeVisible();
});

test("変更なしで再抽出すると検証エラーになる", async ({ page }) => {
  const calls = await mockExperimentWorkspace(page);
  await page.goto(`/documents/${DOC_ID}`);

  await page.getByRole("button", { name: "再抽出して候補を作成" }).click();
  await expect(
    page.getByText("ファイル準備か文書解析のいずれかを現在と違う値に変更してください。")
  ).toBeVisible();
  expect(calls.reparse).toBe(0);
});

test("実験パネルは 375px でも横スクロールしない", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await mockExperimentWorkspace(page);
  await page.goto(`/documents/${DOC_ID}`);

  await expect(page.getByText("別レシピを試す").first()).toBeVisible();
  await page.locator("#experiment-chunk-size").fill("400");
  await page.getByRole("button", { name: "候補を作成", exact: true }).click();
  await expect(page.getByText("候補").first()).toBeVisible();
  await expectNoPageOverflow(page);
});
