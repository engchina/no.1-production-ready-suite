import { expect, type Page, test } from "./fixtures/test";

import { expectNoPageOverflow, mockDatabaseReady, mockLocalAuth, selectSearchAnswerProfile } from "./_helpers";

/**
 * #1160 / #1175: チャットの回答の作成中に配信（SSE）が切れても、回答を受け取れる。
 *
 * backend は接続が切れても回答の作成を続ける（#1175）。画面は最後に受け取った event の連番（`id:`）から
 * 続きを購読し直す（`GET .../messages/{質問の id}/stream`・`Last-Event-ID`）。購読できない（別の worker・
 * 再起動の後）ときは保存済みの会話（作成中の回答）に引き継ぎ、会話の取り直しで完了に変わる。ページを読み込み
 * 直しても作成中の回答は「作成中」で出る。停止は取消の API で止める。
 *
 * `page.route` の fulfill は本文を一度に返すため、画面の fetch を init script で差し替えて、SSE を時間をおいて
 * 流す・途中で閉じる・閉じずに止める（answer-progress.spec.ts と同じ方式）。質問の id は画面が決めるので、
 * 差し替えた fetch が送信の本文から `client_message_id` を読み、`start` と会話の取得に使う。
 */

interface TimedChunk {
  afterMs: number;
  text: string;
}

interface StreamScenario {
  /** 送信（POST）の応答。最後の chunk の後: 閉じる（接続が切れた）・閉じずに止める（配信が途絶えた）。 */
  post: { chunks: TimedChunk[]; end: "close" | "hang" };
  /** 続きの購読（GET）の応答。null は 404（このプロセスで作成していない）。 */
  resume: { chunks: TimedChunk[] } | null;
}

/** SSE の data の中の質問の id（画面が決めた id に置き換える）。 */
const USER_ID = "__USER_ID__";

function sse(id: number, event: string, data: unknown): string {
  return `id: ${id}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

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

const QUESTION = "自分の銀行の得点はなんですか";
const ANSWER = "銀行の得点は 82 点です。";

function userMessage(id: string) {
  return {
    message_id: id,
    conversation_id: "conv-1",
    role: "USER",
    content: QUESTION,
    model: null,
    citations: [],
    guardrail_warnings: [],
    trace_id: null,
    status: "COMPLETE",
    reply_to_message_id: null,
    created_at: new Date(Date.now() - 5_000).toISOString(),
  };
}

const STEP_IDS = ["rewrite_query", "retrieve", "rerank", "generate_answer", "check_guardrail"] as const;
type StepId = (typeof STEP_IDS)[number];

function steps(statuses: Partial<Record<StepId, string>>) {
  const now = new Date().toISOString();
  return STEP_IDS.map((id) => {
    const status = statuses[id] ?? "pending";
    return {
      id,
      label: id,
      status,
      ...(status === "pending" ? {} : { startedAt: now }),
      ...(status === "done" ? { finishedAt: now } : {}),
    };
  });
}

type ReplyState = "streaming" | "complete" | "cancelled";

function reply(userId: string, state: ReplyState) {
  return {
    message_id: "a1",
    conversation_id: "conv-1",
    role: "ASSISTANT",
    content: state === "complete" ? ANSWER : state === "cancelled" ? "回答の作成を停止しました。" : "",
    model: "m1",
    citations: [],
    guardrail_warnings: [],
    trace_id: state === "complete" ? "t1" : null,
    status: state === "complete" ? "COMPLETE" : state === "cancelled" ? "CANCELLED" : "STREAMING",
    reply_to_message_id: userId,
    created_at: new Date(Date.now() - 4_000).toISOString(),
    progress:
      state === "streaming"
        ? steps({ rewrite_query: "done", retrieve: "running" })
        : steps({ rewrite_query: "done", retrieve: "done", rerank: "done", generate_answer: "done", check_guardrail: "done" }),
  };
}

/** 質問を保存して段階を 2 つ進めたところまで（連番 1〜3）。 */
const startedChunks: TimedChunk[] = [
  {
    afterMs: 0,
    text: [
      sse(1, "start", {
        conversation_id: "conv-1",
        user_message: { ...userMessage(USER_ID), created_at: "2026-01-01T00:00:00Z" },
        columns: [{ model_id: "m1", label: "MODEL 1", message_id: "a1" }],
      }),
      sse(2, "progress", { model_id: "m1", steps: steps({ rewrite_query: "running" }) }),
    ].join(""),
  },
  { afterMs: 800, text: sse(3, "progress", { model_id: "m1", steps: steps({ rewrite_query: "done", retrieve: "running" }) }) },
];

/**
 * 続き（連番 4〜）。回答の後、`all_done` は少し待ってから送る（回答が再購読の配信で出たことを確かめてから、
 * テストが保存済みの会話を完了にする）。
 */
const restChunks: TimedChunk[] = [
  {
    afterMs: 300,
    text: [
      sse(4, "progress", {
        model_id: "m1",
        steps: steps({ rewrite_query: "done", retrieve: "done", rerank: "done", generate_answer: "done", check_guardrail: "done" }),
      }),
      sse(5, "metadata", { model_id: "m1", message_id: "a1", trace_id: "t1", elapsed_ms: 1200, guardrail_warnings: [] }),
      sse(6, "delta", { model_id: "m1", text: ANSWER }),
      sse(7, "citations", { model_id: "m1", citations: [] }),
      sse(8, "done", { model_id: "m1", message_id: "a1" }),
    ].join(""),
  },
  { afterMs: 2_000, text: sse(9, "all_done", { conversation_id: "conv-1" }) },
];

async function mockStream(page: Page, scenario: StreamScenario): Promise<void> {
  await page.addInitScript((value: StreamScenario) => {
    const win = window as unknown as {
      __clientMessageId?: string;
      __resumeRequests: { path: string; lastEventId: string | null }[];
    };
    win.__resumeRequests = [];
    const originalFetch = window.fetch.bind(window);
    const respond = (chunks: TimedChunk[], end: "close" | "hang", signal?: AbortSignal) => {
      const encoder = new TextEncoder();
      const stream = new ReadableStream<Uint8Array>({
        async start(controller) {
          signal?.addEventListener("abort", () => {
            try {
              controller.error(new DOMException("aborted", "AbortError"));
            } catch {
              // 閉じた後の中止は無視する。
            }
          });
          for (const chunk of chunks) {
            if (chunk.afterMs) await new Promise((resolve) => setTimeout(resolve, chunk.afterMs));
            if (signal?.aborted) return;
            controller.enqueue(encoder.encode(chunk.text.replaceAll("__USER_ID__", win.__clientMessageId ?? "u1")));
          }
          if (end === "close") controller.close();
        },
      });
      return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
    };
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const path = new URL(url, window.location.href).pathname;
      const signal = init?.signal ?? undefined;
      if (path.endsWith("/messages/stream") && init?.method === "POST") {
        const body = JSON.parse(String(init.body ?? "{}")) as { client_message_id?: string };
        win.__clientMessageId = body.client_message_id;
        return respond(value.post.chunks, value.post.end, signal);
      }
      if (/\/messages\/[^/]+\/stream$/.test(path)) {
        const headers = new Headers(init?.headers);
        win.__resumeRequests.push({ path, lastEventId: headers.get("Last-Event-ID") });
        if (value.resume === null) {
          return new Response(JSON.stringify({ data: null, error_messages: ["再開できません"], warning_messages: [] }), {
            status: 404,
            headers: { "content-type": "application/json" },
          });
        }
        return respond(value.resume.chunks, "close", signal);
      }
      return originalFetch(input, init);
    };
  }, scenario);
}

interface ChatState {
  /** 会話の取得で返す回答の状態（null は回答なし）。 */
  reply: ReplyState | null;
  conversationRequests: number;
  cancelRequests: string[];
  /** 会話の取得を失敗させる。 */
  conversationFails: boolean;
  /** 最初から会話に質問がある（再読込のテスト）。 */
  existingUserId: string | null;
}

async function mockChatPage(page: Page, scenario: StreamScenario | null, initial: Partial<ChatState> = {}) {
  const state: ChatState = {
    reply: null,
    conversationRequests: 0,
    cancelRequests: [],
    conversationFails: false,
    existingUserId: null,
    ...initial,
  };
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
  let created = state.existingUserId !== null;
  await page.route("**/api/chat/conversations**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/cancel") && request.method() === "POST") {
      state.cancelRequests.push(path);
      state.reply = "cancelled";
      await route.fulfill({ json: { data: { cancelled: true }, error_messages: [], warning_messages: [] } });
      return;
    }
    const userId =
      state.existingUserId ??
      (await page.evaluate(() => (window as unknown as { __clientMessageId?: string }).__clientMessageId ?? null));
    const messages = created && userId ? [userMessage(userId), ...(state.reply ? [reply(userId, state.reply)] : [])] : [];
    const detail = {
      id: "conv-1",
      search_answer_profile_id: "bv-1",
      title: created ? QUESTION : null,
      status: "ACTIVE",
      message_count: messages.length,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:02Z",
      messages,
    };
    if (path === "/api/chat/conversations" && request.method() === "POST") {
      created = true;
      await route.fulfill({
        json: { data: { ...detail, messages: [] }, error_messages: [], warning_messages: [] },
      });
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
    state.conversationRequests += 1;
    if (state.conversationFails) {
      // 通信が戻らない（会話も取り直せない）。
      await route.abort("failed");
      return;
    }
    await route.fulfill({ json: { data: detail, error_messages: [], warning_messages: [] } });
  });
  if (scenario) await mockStream(page, scenario);
  return state;
}

async function send(page: Page) {
  await page.goto("/chat");
  await selectSearchAnswerProfile(page, "経理ビュー");
  await page.getByRole("textbox").fill(QUESTION);
  await page.getByRole("button", { name: "送信" }).click();
  const current = page.getByTestId("chat-answer-progress-current");
  await expect(current).toContainText("関係する文書を探しています", { timeout: 5_000 });
  return current;
}

async function resumeRequests(page: Page) {
  return page.evaluate(
    () => (window as unknown as { __resumeRequests: { path: string; lastEventId: string | null }[] }).__resumeRequests
  );
}

async function applyTheme(page: Page, colorScheme: "light" | "dark") {
  // 外観の既定はライトなので、ダークは画面の属性でも切り替える（answer-progress.spec.ts と同じ）。
  await page.evaluate((value) => {
    document.documentElement.dataset.theme = value;
  }, colorScheme);
}

for (const colorScheme of ["light", "dark"] as const) {
  {
    // desktop / 375px は playwright.config の project（desktop・mobile）で確かめる。
    test.describe(`チャットの回答の再接続（${colorScheme}）`, () => {
      test.beforeEach(async ({ page }) => {
        await page.emulateMedia({ colorScheme });
      });

      test("all_done の前に接続が切れたら、続きから購読し直して回答が出る（#1175）", async ({ page }, testInfo) => {
        const state = await mockChatPage(
          page,
          {
            post: { chunks: [...startedChunks, { afterMs: 400, text: "" }], end: "close" },
            resume: { chunks: restChunks },
          },
          { reply: "streaming" }
        );
        await send(page);
        await applyTheme(page, colorScheme);
        // 回答は購読し直した配信で届く（保存済みの会話はまだ作成中）。
        await expect(page.getByTestId("chat-live-turn").getByText(ANSWER)).toBeVisible({ timeout: 10_000 });
        await page.screenshot({ path: testInfo.outputPath(`chat-resumed-${colorScheme}.png`) });
        state.reply = "complete";
        await expect(page.getByTestId("chat-live-turn")).toHaveCount(0, { timeout: 10_000 });
        await expect(page.getByText(ANSWER).first()).toBeVisible();
        // 最後に受け取った event（連番 3）の次から、送った質問の id で購読し直す。
        const requests = await resumeRequests(page);
        const clientId = await page.evaluate(() => (window as unknown as { __clientMessageId: string }).__clientMessageId);
        expect(clientId).toMatch(/^[0-9a-f]{32}$/);
        expect(requests[0]).toEqual({ path: `/api/chat/conversations/conv-1/messages/${clientId}/stream`, lastEventId: "3" });
        await expect(page.getByTestId("chat-send-failure")).toHaveCount(0);
        await expect(page.getByTestId("chat-answer-progress-current")).toHaveCount(0);
        await expect(page.getByRole("button", { name: "送信" })).toBeVisible();
        await expectNoPageOverflow(page);
      });

      test("続きを購読できないときは、保存済みの作成中の回答に引き継ぎ、完了したら回答に置き換わる（#1175）", async ({
        page,
      }, testInfo) => {
        const state = await mockChatPage(
          page,
          { post: { chunks: [...startedChunks, { afterMs: 400, text: "" }], end: "close" }, resume: null },
          { reply: "streaming" }
        );
        await send(page);
        // 保存済みの作成中の回答（今の段階）が出て、送信のボタンは「停止」のまま。
        const saved = page.getByTestId("chat-saved-streaming-turn");
        await expect(saved).toBeVisible({ timeout: 10_000 });
        await expect(saved.getByTestId("chat-answer-progress-current")).toContainText("関係する文書を探しています");
        await expect(page.getByTestId("chat-live-turn")).toHaveCount(0);
        await expect(page.getByTestId("chat-run-stop")).toHaveAccessibleName("停止");
        await applyTheme(page, colorScheme);
        await saved.scrollIntoViewIfNeeded();
        await page.screenshot({ path: testInfo.outputPath(`chat-saved-streaming-${colorScheme}.png`) });
        await expectNoPageOverflow(page);
        // 作成が終わると、会話の取り直し（polling）で回答に置き換わる。
        const before = state.conversationRequests;
        state.reply = "complete";
        await expect(page.getByText(ANSWER).first()).toBeVisible({ timeout: 10_000 });
        expect(state.conversationRequests).toBeGreaterThan(before);
        await expect(saved).toHaveCount(0);
        await expect(page.getByTestId("chat-run-stop")).toHaveAccessibleName("送信");
        await expect(page.getByTestId("chat-send-failure")).toHaveCount(0);
      });

      test("ページを読み込み直すと、作成中の回答は作成中で出て、完了したら回答に変わる（#1175）", async ({ page }) => {
        const state = await mockChatPage(page, null, { reply: "streaming", existingUserId: "u-saved" });
        await page.goto("/chat?search_answer_profile_id=bv-1&conversation_id=conv-1");
        const saved = page.getByTestId("chat-saved-streaming-turn");
        await expect(saved.getByTestId("chat-answer-progress-current")).toContainText("関係する文書を探しています", {
          timeout: 10_000,
        });
        await expect(page.getByTestId("chat-run-stop")).toHaveAccessibleName("停止");
        await applyTheme(page, colorScheme);
        await expectNoPageOverflow(page);
        state.reply = "complete";
        await expect(page.getByText(ANSWER).first()).toBeVisible({ timeout: 10_000 });
        await expect(saved).toHaveCount(0);
        // 保存した処理の経過は、回答の上に 1 行で残る。
        await expect(page.getByTestId("chat-answer-progress")).toBeVisible();
        await expect(page.getByTestId("chat-run-stop")).toHaveAccessibleName("送信");
      });
    });
  }

  test.describe(`チャットの回答の停止と途絶え（${colorScheme}）`, () => {
    test.beforeEach(async ({ page }) => {
      await page.emulateMedia({ colorScheme });
    });

    test("作成中に停止すると、送った質問の id で取消の API を呼ぶ（#1175）", async ({ page }) => {
      const state = await mockChatPage(page, { post: { chunks: startedChunks, end: "hang" }, resume: null });
      await send(page);
      await page.getByTestId("chat-run-stop").click();
      await expect(page.getByTestId("chat-stopped")).toBeVisible();
      const clientId = await page.evaluate(() => (window as unknown as { __clientMessageId: string }).__clientMessageId);
      await expect.poll(() => state.cancelRequests).toEqual([`/api/chat/conversations/conv-1/messages/${clientId}/cancel`]);
      // 接続を切っただけでは続きを購読し直さない（停止は取消）。
      expect(await resumeRequests(page)).toEqual([]);
      await expect(page.getByTestId("chat-run-stop")).toHaveAccessibleName("送信");
      await applyTheme(page, colorScheme);
      await expectNoPageOverflow(page);
    });

    test("読み込み直した後の作成中の回答も「停止」で止められ、停止した回答が出る（#1175）", async ({ page }) => {
      const state = await mockChatPage(page, null, { reply: "streaming", existingUserId: "u-saved" });
      await page.goto("/chat?search_answer_profile_id=bv-1&conversation_id=conv-1");
      const button = page.getByTestId("chat-run-stop");
      await expect(button).toHaveAccessibleName("停止", { timeout: 10_000 });
      await button.click();
      await expect.poll(() => state.cancelRequests).toEqual(["/api/chat/conversations/conv-1/messages/u-saved/cancel"]);
      await expect(page.getByTestId("chat-answer-stopped")).toHaveText("回答の作成を停止しました。");
      await expect(button).toHaveAccessibleName("送信");
      await applyTheme(page, colorScheme);
      await expectNoPageOverflow(page);
    });

    test("続きを購読できず会話も取り直せなければ、理由と「再送信」を出す", async ({ page }) => {
      const state = await mockChatPage(page, {
        post: { chunks: [...startedChunks, { afterMs: 400, text: "" }], end: "close" },
        resume: null,
      });
      state.conversationFails = true;
      await send(page);
      const failure = page.getByTestId("chat-send-failure");
      await expect(failure).toContainText("回答を受け取る途中で接続が切れ", { timeout: 20_000 });
      await expect(failure.getByRole("button", { name: "再送信" })).toBeVisible();
      await expect(page.getByTestId("chat-answer-progress-current")).toHaveCount(0);
      await applyTheme(page, colorScheme);
      await expectNoPageOverflow(page);
    });

    test("配信が途絶えたら「接続を確認しています」を出して接続を張り直し、続きを受け取る", async ({
      page,
    }, testInfo) => {
      test.setTimeout(90_000);
      const state = await mockChatPage(
        page,
        { post: { chunks: startedChunks, end: "hang" }, resume: { chunks: restChunks } },
        { reply: "streaming" }
      );
      const current = await send(page);
      const reconnecting = page.getByTestId("chat-answer-progress-reconnecting");
      await expect(reconnecting).toHaveText("接続を確認しています。", { timeout: 40_000 });
      // 今の段階の行は残したまま、遅延の案内の代わりに出す。
      await expect(current).toContainText("関係する文書を探しています");
      await expect(current).toHaveAttribute("data-reconnecting", "true");
      await current.scrollIntoViewIfNeeded();
      await applyTheme(page, colorScheme);
      await page.screenshot({ path: testInfo.outputPath(`chat-reconnecting-${colorScheme}.png`) });
      await expectNoPageOverflow(page);
      // 回答の作成は続いていた。張り直した接続で続き（連番 4〜）を受け取り、回答が出る。
      await expect(page.getByTestId("chat-live-turn").getByText(ANSWER)).toBeVisible({ timeout: 20_000 });
      expect((await resumeRequests(page))[0]?.lastEventId).toBe("3");
      state.reply = "complete";
      await expect(page.getByTestId("chat-live-turn")).toHaveCount(0, { timeout: 10_000 });
      await expect(reconnecting).toHaveCount(0);
      await expect(page.getByRole("button", { name: "送信" })).toBeVisible();
    });
  });
}
