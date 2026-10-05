import { expect, type Page, test } from "./fixtures/test";

import {
  enableSearchAnswer,
  expectNoPageOverflow,
  mockDatabaseReady,
  mockLocalAuth,
  selectSearchAnswerProfile,
} from "./_helpers";
import { expectProgressTimerMonotonic, startProgressTimerSampler } from "./_progress-timer";

/**
 * 回答生成の進捗（今の工程と経過時間）と時間切れの表示（#375）。
 *
 * `page.route` の fulfill は本文を一度に返すため、SSE の event を時間をおいて届けるよう、
 * 画面の fetch を init script で差し替えて ReadableStream で少しずつ流す。
 */

interface TimedChunk {
  afterMs: number;
  text: string;
}

type StreamScenarios = Record<"search" | "chat", TimedChunk[][]>;

function sse(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

const TIMEOUT_MESSAGE =
  "回答の生成が上限の 5 分以内に終わりませんでした（時間切れになった工程: 文書検索）。" +
  "時間をおいて、もう一度送信してください。";

const searchAnswerProfile = {
  id: "bv-1",
  name: "経理ビュー",
  description: null,
  status: "ACTIVE",
  knowledge_base_count: 1,
  created_at: "2026-06-19T00:00:00Z",
  updated_at: "2026-06-19T00:00:00Z",
  archived_at: null,
};

const citation = {
  document_id: "doc-1",
  chunk_id: "doc-1:0",
  text: "銀行の得点は 82 点です。",
  score: 0.9,
  rerank_score: 0.8,
  file_name: "評価表.pdf",
  category_name: null,
  metadata: {},
};

function searchStage(stage: string, outcome: string, elapsed_ms = 0) {
  return sse("stage", { trace_id: "trace-1", stage, outcome, elapsed_ms, attributes: {} });
}

/**
 * 根拠の検索と回答の生成（answer）の中を、質問の理解 → 文書検索 と時間をおいて進む検索の SSE。
 * 中の各工程は `answer_step:` の入れ子の工程として届く（#593）。
 */
const slowSearchStream: TimedChunk[] = [
  {
    afterMs: 0,
    text: [
      searchStage("answer", "started"),
      searchStage("answer_step:質問の理解", "started"),
    ].join(""),
  },
  {
    afterMs: 1500,
    text: [
      searchStage("answer_step:質問の理解", "success", 1500),
      searchStage("answer_step:文書検索", "started"),
    ].join(""),
  },
  {
    afterMs: 1500,
    text: [
      searchStage("answer_step:文書検索", "success", 1500),
      searchStage("answer_step:回答の生成", "started"),
    ].join(""),
  },
  {
    afterMs: 600,
    text: [
      searchStage("answer_step:回答の生成", "success", 600),
      searchStage("answer", "success", 3600),
      sse("metadata", {
        trace_id: "trace-1",
        elapsed_ms: 3600,
        guardrail_warnings: [],
        diagnostics: {
          retrieval_strategy: "hybrid",
          retrieval_strategy_adapter: "grounded",
          filter_keys: [],
          knowledge_base_count: 1,
          config_fingerprint: "fp-1",
        },
      }),
      sse("delta", { text: "銀行の得点は 82 点です。" }),
      sse("citations", [citation]),
      sse("done", { trace_id: "trace-1" }),
    ].join(""),
  },
];

const timedOutSearchStream: TimedChunk[] = [
  {
    afterMs: 0,
    text: [
      searchStage("answer", "started"),
      searchStage("answer_step:文書検索", "started"),
    ].join(""),
  },
  {
    afterMs: 800,
    text: sse("error", {
      trace_id: "trace-1",
      message: TIMEOUT_MESSAGE,
      error_type: "TimeoutError",
      stage: "answer_step:文書検索",
    }),
  },
];

async function mockStreams(page: Page, scenarios: StreamScenarios): Promise<void> {
  await page.addInitScript((scenarioMap: StreamScenarios) => {
    const w = window as unknown as {
      __sseRequests: Array<{ key: string; body: unknown }>;
    };
    w.__sseRequests = [];
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url =
        typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const key = url.includes("/api/search/stream")
        ? "search"
        : url.includes("/messages/stream")
          ? "chat"
          : null;
      if (!key) return originalFetch(input, init);
      w.__sseRequests.push({ key, body: init?.body ? JSON.parse(String(init.body)) : null });
      const index = w.__sseRequests.filter((request) => request.key === key).length - 1;
      const list = scenarioMap[key];
      const chunks = list[Math.min(index, list.length - 1)];
      const encoder = new TextEncoder();
      const signal = init?.signal ?? undefined;
      const stream = new ReadableStream<Uint8Array>({
        async start(controller) {
          for (const chunk of chunks) {
            if (chunk.afterMs) await new Promise((resolve) => setTimeout(resolve, chunk.afterMs));
            if (signal?.aborted) {
              controller.error(new DOMException("aborted", "AbortError"));
              return;
            }
            // 段階の時刻（`__NOW__`）は送った時刻にする（段階の時刻で経過時間を数え直すと減ることを確かめる。#1176）。
            controller.enqueue(encoder.encode(chunk.text.replaceAll("__NOW__", new Date().toISOString())));
          }
          controller.close();
        },
      });
      return new Response(stream, {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      });
    };
  }, scenarios);
}

async function streamRequests(page: Page, key: "search" | "chat") {
  return page.evaluate(
    (requestKey) =>
      (
        window as unknown as { __sseRequests: Array<{ key: string; body: unknown }> }
      ).__sseRequests.filter((request) => request.key === requestKey),
    key
  );
}

async function mockSearchPage(page: Page, scenarios: TimedChunk[][]) {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
  await page.route("**/api/search-answer-profiles**", (route) =>
    route.fulfill({
      json: {
        data: { items: [searchAnswerProfile], total: 1, limit: 50, offset: 0, has_next: false },
        error_messages: [],
        warning_messages: [],
      },
    })
  );
  await mockStreams(page, { search: scenarios, chat: [[]] });
  await page.goto("/search");
  await selectSearchAnswerProfile(page, /経理ビュー/);
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`RAG 検索は回答生成中に今の工程と経過時間を出す (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockSearchPage(page, [slowSearchStream]);

    await enableSearchAnswer(page);
    await page.getByRole("textbox", { name: "RAG 検索" }).fill("自分の銀行の得点はなんですか");
    await page.getByRole("button", { name: "検索", exact: true }).click();

    const progress = page.getByTestId("search-run-progress");
    const timer = page.getByTestId("search-run-progress-timer");
    // 回答の中の工程（LLM）の間も止まって見えない。
    await expect(progress).toContainText("回答を生成しています（質問の理解）");
    await expect(timer).toContainText("経過時間");
    const firstElapsed = await timer.textContent();
    await expect(progress).toContainText("回答を生成しています（文書検索）", {
      timeout: 4_000,
    });
    await expect.poll(() => timer.textContent(), { timeout: 4_000 }).not.toBe(firstElapsed);
    // 検索のボタンは実行中に「停止」になりスピナーを出さないため、動くスピナーは進捗の表示の 1 つだけ（#413）。
    await expect(progress).toHaveAttribute("data-processing-activity-icon", "spinner");
    await expect(page.getByTestId("search-run-stop")).toHaveAccessibleName("停止");
    await expect(page.getByTestId("search-run-stop")).not.toHaveAttribute("aria-busy", "true");
    await expect(progress).toHaveAttribute("data-processing-placement", "result");
    await page.screenshot({
      path: `test-results/answer-progress-search-${viewport.name}.png`,
      fullPage: false,
    });

    await expect(progress).toContainText("回答を生成しました", { timeout: 4_000 });
    await expect(timer).toContainText("処理時間");
    await expect(page.getByText("銀行の得点は 82 点です。").first()).toBeVisible();
    const runPanel = page.getByRole("region", { name: "検索実行" });
    await expect(runPanel.getByText("根拠の検索と回答の生成", { exact: true })).toBeVisible();
    await expect(runPanel.getByText("文書検索", { exact: true })).toBeVisible();
    await expectNoPageOverflow(page);
  });

  test(`RAG 検索が時間切れになったら工程と再試行を示す (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockSearchPage(page, [timedOutSearchStream, slowSearchStream]);

    await enableSearchAnswer(page);
    await page.getByRole("textbox", { name: "RAG 検索" }).fill("自分の銀行の得点はなんですか");
    await page.getByRole("button", { name: "検索", exact: true }).click();

    await expect(page.getByTestId("search-run-progress")).toContainText("文書検索");
    const error = page.getByRole("alert").filter({ hasText: "時間切れになった工程: 文書検索" });
    await expect(error).toBeVisible({ timeout: 4_000 });
    await expect(error).toContainText("もう一度送信してください");
    await page.screenshot({
      path: `test-results/answer-progress-search-timeout-${viewport.name}.png`,
      fullPage: false,
    });
    await expectNoPageOverflow(page);

    await error.getByRole("button", { name: "再試行" }).click();
    await expect(page.getByTestId("search-run-progress")).toContainText("回答を生成しました", {
      timeout: 8_000,
    });
    expect(await streamRequests(page, "search")).toHaveLength(2);
  });
}

// --- チャット ---

const userMessage = {
  message_id: "u1",
  conversation_id: "conv-1",
  role: "USER",
  content: "自分の銀行の得点はなんですか",
  model: null,
  citations: [],
  guardrail_warnings: [],
  trace_id: null,
  status: "COMPLETE",
  reply_to_message_id: null,
  created_at: "2026-01-01T00:00:00Z",
};

const errorReply = {
  message_id: "a1",
  conversation_id: "conv-1",
  role: "ASSISTANT",
  content: TIMEOUT_MESSAGE,
  model: "m1",
  citations: [],
  guardrail_warnings: [],
  trace_id: "t1",
  status: "ERROR",
  reply_to_message_id: "u1",
  created_at: "2026-01-01T00:00:01Z",
};

const retryUserMessage = { ...userMessage, message_id: "u2", created_at: "2026-01-01T00:01:00Z" };
const okReply = {
  ...errorReply,
  message_id: "a2",
  content: "銀行の得点は 82 点です。",
  citations: [citation],
  trace_id: "t2",
  status: "COMPLETE",
  reply_to_message_id: "u2",
  created_at: "2026-01-01T00:01:05Z",
};

function chatStage(stage: string, outcome: string, elapsed_ms = 0) {
  return sse("stage", { model_id: "m1", trace_id: "t1", stage, outcome, elapsed_ms });
}

/** ライト・ダークの両方の画面を残す（処理の段階の色は共有のトークン。#1146）。 */
async function screenshotBothThemes(page: Page, path: (theme: string) => string) {
  for (const theme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: theme });
    await page.evaluate((value) => {
      document.documentElement.dataset.theme = value;
    }, theme);
    await page.screenshot({ path: path(theme), fullPage: false });
  }
}

const CHAT_STEP_IDS = ["rewrite_query", "retrieve", "rerank", "generate_answer", "check_guardrail"] as const;

/** 処理の段階（3 製品共通の ChatProgressStep。#1146）。`statuses` に無い段階は未開始。 */
function chatProgress(statuses: Partial<Record<(typeof CHAT_STEP_IDS)[number], string>>) {
  // 配信した時刻に置き換える（mockStreams）。
  const now = "__NOW__";
  return sse("progress", {
    model_id: "m1",
    steps: CHAT_STEP_IDS.map((id) => {
      const status = statuses[id] ?? "pending";
      return {
        id,
        // 画面は段階の id と状態から名前を付ける（backend の名前は未知の段階だけに使う）。
        label: `backend の名前 ${id}`,
        status,
        ...(status === "pending" || status === "skipped" ? {} : { startedAt: now }),
        ...(status === "done" || status === "failed" ? { finishedAt: now } : {}),
        ...(id === "retrieve" && status === "done" ? { detail: "根拠 1 件" } : {}),
      };
    }),
  });
}

const timedOutChatStream: TimedChunk[] = [
  {
    afterMs: 0,
    text: [
      sse("start", {
        conversation_id: "conv-1",
        user_message: userMessage,
        columns: [{ model_id: "m1", label: "MODEL 1" }],
      }),
      chatProgress({}),
      chatStage("answer", "started"),
      chatStage("answer_step:質問の理解", "started"),
      chatProgress({ rewrite_query: "running" }),
    ].join(""),
  },
  {
    afterMs: 1500,
    text: [
      chatStage("answer_step:質問の理解", "success", 1500),
      chatStage("answer_step:文書検索", "started"),
      chatProgress({ rewrite_query: "done", retrieve: "running" }),
    ].join(""),
  },
  {
    afterMs: 1500,
    text: [
      chatStage("answer_step:文書検索", "cancelled", 1500),
      chatStage("answer", "cancelled", 1500),
      chatProgress({ rewrite_query: "done", retrieve: "failed" }),
      sse("error", {
        model_id: "m1",
        message: TIMEOUT_MESSAGE,
        error_type: "AnswerTimeoutError",
        stage: "answer_step:文書検索",
      }),
    ].join(""),
  },
  {
    // 失敗した段階を確かめる間をとってから、会話を取り直す。
    afterMs: 1500,
    text: sse("all_done", { conversation_id: "conv-1" }),
  },
];

const okChatStream: TimedChunk[] = [
  {
    afterMs: 0,
    text: [
      sse("start", {
        conversation_id: "conv-1",
        user_message: retryUserMessage,
        columns: [{ model_id: "m1", label: "MODEL 1" }],
      }),
      chatStage("answer", "started"),
      chatProgress({ rewrite_query: "running" }),
    ].join(""),
  },
  {
    afterMs: 500,
    text: [
      chatStage("answer", "success", 500),
      chatProgress({
        rewrite_query: "done",
        retrieve: "done",
        rerank: "done",
        generate_answer: "done",
        check_guardrail: "done",
      }),
      sse("metadata", {
        model_id: "m1",
        message_id: "a2",
        trace_id: "t2",
        elapsed_ms: 500,
        guardrail_warnings: [],
      }),
      sse("delta", { model_id: "m1", text: okReply.content }),
      sse("citations", { model_id: "m1", citations: [citation] }),
      sse("done", { model_id: "m1", message_id: "a2" }),
      sse("all_done", { conversation_id: "conv-1" }),
    ].join(""),
  },
];

async function mockChatPage(page: Page) {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
  await page.route("**/api/search-answer-profiles**", (route) =>
    route.fulfill({
      json: {
        data: { items: [searchAnswerProfile], total: 1, limit: 50, offset: 0, has_next: false },
        error_messages: [],
        warning_messages: [],
      },
    })
  );
  await page.route("**/api/chat/models", (route) =>
    route.fulfill({ json: { data: [], error_messages: [], warning_messages: [] } })
  );
  let created = false;
  await page.route("**/api/chat/conversations**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const sent = (await streamRequests(page, "chat").catch(() => [])).length;
    const messages =
      sent >= 2
        ? [userMessage, errorReply, retryUserMessage, okReply]
        : sent === 1
          ? [userMessage, errorReply]
          : [];
    const detail = {
      id: "conv-1",
      search_answer_profile_id: "bv-1",
      title: sent ? userMessage.content : null,
      status: "ACTIVE",
      message_count: messages.length,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:02Z",
      messages,
    };
    if (path === "/api/chat/conversations" && request.method() === "POST") {
      created = true;
      await route.fulfill({ json: { data: detail, error_messages: [], warning_messages: [] } });
      return;
    }
    if (path === "/api/chat/conversations") {
      const items = created ? [{ ...detail, messages: undefined }] : [];
      await route.fulfill({
        json: {
          data: { items, total: items.length, limit: 50, offset: 0, has_next: false },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }
    await route.fulfill({ json: { data: detail, error_messages: [], warning_messages: [] } });
  });
  await mockStreams(page, { search: [[]], chat: [timedOutChatStream, okChatStream] });
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`チャットは回答生成の工程と経過時間を出し、時間切れを再送できる (${viewport.name})`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockChatPage(page);

    await page.goto("/chat");
    await selectSearchAnswerProfile(page, "経理ビュー");
    await page.getByRole("button", { name: "新しい会話" }).click();
    await page.getByRole("textbox").fill(userMessage.content);
    // #1176: 段階が進む間、右上の経過時間（送信からの通算）が減らないことを記録する。
    await startProgressTimerSampler(page, "chat-answer-progress");
    await page.getByRole("button", { name: "送信" }).click();

    // 処理の段階（3 製品共通の ChatProgress。#1146）。今の段階を 1 行で出し、段階が進むと入れ替わる。
    const progress = page.getByTestId("chat-answer-progress");
    const current = page.getByTestId("chat-answer-progress-current");
    const timer = page.getByTestId("chat-answer-progress-timer");
    await expect(current).toContainText("質問を整理しています");
    await expect(timer).toContainText("経過時間");
    const firstElapsed = await timer.textContent();
    await expect(current).toContainText("関係する文書を探しています", {
      timeout: 4_000,
    });
    await expect(page.getByTestId("chat-answer-progress-completed")).toHaveText("1 ステップ完了");
    await expect.poll(() => timer.textContent(), { timeout: 4_000 }).not.toBe(firstElapsed);
    await progress.scrollIntoViewIfNeeded();
    await page.screenshot({
      path: `test-results/answer-progress-chat-${viewport.name}.png`,
      fullPage: false,
    });

    // 時間切れの文言（工程と再試行の案内）は ERROR の回答として残り、同じ質問を送り直せる。
    const error = page.getByRole("alert").filter({ hasText: "時間切れになった工程: 文書検索" });
    await expect(error).toBeVisible({ timeout: 5_000 });
    // 質問の整理・文書の検索の段階を通る間、経過時間は減らない（段階ごとに 0 に戻さない。#1176）。
    await expectProgressTimerMonotonic(page, 2);
    // 止まった段階を開いて出す（原因は段階ではなく Banner に出す）。
    await expect(progress).toHaveAttribute("data-chat-progress-state", "failed");
    await expect(page.getByTestId("chat-answer-progress-step-retrieve")).toContainText(
      "関係する文書を探せませんでした"
    );
    await progress.scrollIntoViewIfNeeded();
    await screenshotBothThemes(page, (theme) => `test-results/answer-progress-chat-failed-${viewport.name}-${theme}.png`);
    // 会話を取り直すと保存した失敗の回答に置き換わる（段階は保存しない）。
    await expect(progress).toHaveCount(0);
    await expect(error).toBeVisible();
    await error.scrollIntoViewIfNeeded();
    await page.screenshot({
      path: `test-results/answer-progress-chat-timeout-${viewport.name}.png`,
      fullPage: false,
    });
    await expectNoPageOverflow(page);

    await error.getByRole("button", { name: "もう一度送信" }).click();
    await expect(page.getByText(okReply.content).first()).toBeVisible({ timeout: 5_000 });
    // 完了した回答の上に「処理の経過」の 1 行を残す（既定は閉じる。#1146）。
    const summary = page.getByTestId("chat-answer-progress-summary");
    await expect(summary).toContainText("処理の経過（5 ステップ・");
    await expect(page.getByTestId("chat-answer-progress-step-retrieve")).toBeHidden();
    await summary.click();
    await expect(page.getByTestId("chat-answer-progress-step-retrieve")).toContainText(
      "関係する文書を探しました（根拠 1 件）"
    );
    await summary.scrollIntoViewIfNeeded();
    await screenshotBothThemes(page, (theme) => `test-results/answer-progress-chat-done-${viewport.name}-${theme}.png`);
    await expectNoPageOverflow(page);
    const requests = await streamRequests(page, "chat");
    expect(requests).toHaveLength(2);
    expect(requests[1].body).toMatchObject({ content: userMessage.content });
    // 再送に成功したら、最新のターンではなくなった失敗には再送の操作を出さない。
    await expect(page.getByRole("button", { name: "もう一度送信" })).toHaveCount(0);
  });
}
