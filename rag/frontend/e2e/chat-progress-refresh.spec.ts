import { expect, type Page, test } from "./fixtures/test";

import { expectNoPageOverflow, mockDatabaseReady, mockLocalAuth, selectSearchAnswerProfile } from "./_helpers";

/**
 * #1160: チャットの回答の作成中に、配信（SSE）が途絶えた・`all_done` の前に終わったときも、画面を止めない。
 * 保存済みの会話を取り直して回答に置き換え、無ければ理由と「再送信」を出す。
 *
 * `page.route` の fulfill は本文を一度に返すため、画面の fetch を init script で差し替えて、SSE を時間をおいて
 * 流す・途中で閉じる・閉じずに止める（answer-progress.spec.ts と同じ方式）。
 */

interface TimedChunk {
  afterMs: number;
  text: string;
}

interface StreamScenario {
  chunks: TimedChunk[];
  /** 最後の chunk の後: 閉じる（接続が切れた）・閉じずに止める（配信が途絶えた）。 */
  end: "close" | "hang";
}

function sse(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
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

const savedReply = {
  message_id: "a1",
  conversation_id: "conv-1",
  role: "ASSISTANT",
  content: "銀行の得点は 82 点です。",
  model: "m1",
  citations: [],
  guardrail_warnings: [],
  trace_id: "t1",
  status: "COMPLETE",
  reply_to_message_id: "u1",
  created_at: "2026-01-01T00:00:05Z",
};

const STEP_IDS = ["rewrite_query", "retrieve", "rerank", "generate_answer", "check_guardrail"] as const;

function progress(statuses: Partial<Record<(typeof STEP_IDS)[number], string>>) {
  const now = new Date().toISOString();
  return sse("progress", {
    model_id: "m1",
    steps: STEP_IDS.map((id) => {
      const status = statuses[id] ?? "pending";
      return {
        id,
        label: id,
        status,
        ...(status === "pending" ? {} : { startedAt: now }),
        ...(status === "done" ? { finishedAt: now } : {}),
      };
    }),
  });
}

/** 質問を保存して段階を 2 つ進めたところまで。 */
const startedChunks: TimedChunk[] = [
  {
    afterMs: 0,
    text: [
      sse("start", {
        conversation_id: "conv-1",
        user_message: userMessage,
        columns: [{ model_id: "m1", label: "MODEL 1" }],
      }),
      progress({ rewrite_query: "running" }),
    ].join(""),
  },
  { afterMs: 800, text: progress({ rewrite_query: "done", retrieve: "running" }) },
];

async function mockStream(page: Page, scenario: StreamScenario): Promise<void> {
  await page.addInitScript((value: StreamScenario) => {
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (!url.includes("/messages/stream")) return originalFetch(input, init);
      const encoder = new TextEncoder();
      const signal = init?.signal ?? undefined;
      const stream = new ReadableStream<Uint8Array>({
        async start(controller) {
          signal?.addEventListener("abort", () => {
            try {
              controller.error(new DOMException("aborted", "AbortError"));
            } catch {
              // 閉じた後の中止は無視する。
            }
          });
          for (const chunk of value.chunks) {
            if (chunk.afterMs) await new Promise((resolve) => setTimeout(resolve, chunk.afterMs));
            if (signal?.aborted) return;
            controller.enqueue(encoder.encode(chunk.text));
          }
          // 閉じずに止める（バイトが届かない）か、all_done を送らずに閉じる（接続が切れた）。
          if (value.end === "close") controller.close();
        },
      });
      return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
    };
  }, scenario);
}

/** 会話の取得。`saved` が true になったら保存済みの回答を返す。 */
async function mockChatPage(page: Page, scenario: StreamScenario) {
  const state = { saved: false, conversationRequests: 0 };
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
    const messages = created ? (state.saved ? [userMessage, savedReply] : [userMessage]) : [];
    const detail = {
      id: "conv-1",
      search_answer_profile_id: "bv-1",
      title: created ? userMessage.content : null,
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
    await route.fulfill({ json: { data: detail, error_messages: [], warning_messages: [] } });
  });
  await mockStream(page, scenario);
  return state;
}

async function send(page: Page) {
  await page.goto("/chat");
  await selectSearchAnswerProfile(page, "経理ビュー");
  await page.getByRole("textbox").fill(userMessage.content);
  await page.getByRole("button", { name: "送信" }).click();
  const current = page.getByTestId("chat-answer-progress-current");
  await expect(current).toContainText("関係する文書を探しています", { timeout: 5_000 });
  return current;
}

for (const colorScheme of ["light", "dark"] as const) {
  test.describe(`チャットの処理の経過の取り直し（${colorScheme}）`, () => {
    test.beforeEach(async ({ page }) => {
      await page.emulateMedia({ colorScheme });
    });

    test("all_done の前に接続が切れても、保存済みの回答に置き換わる", async ({ page }) => {
      const state = await mockChatPage(page, {
        chunks: [...startedChunks, { afterMs: 600, text: "" }],
        end: "close",
      });
      state.saved = true;
      await send(page);
      await expect(page.getByText(savedReply.content).first()).toBeVisible({ timeout: 10_000 });
      await expect(page.getByTestId("chat-live-turn")).toHaveCount(0);
      await expect(page.getByTestId("chat-send-failure")).toHaveCount(0);
      await expect(page.getByTestId("chat-answer-progress-current")).toHaveCount(0);
      // 送信できる状態に戻る（停止のまま止まらない）。
      await expect(page.getByRole("button", { name: "送信" })).toBeVisible();
      await expectNoPageOverflow(page);
    });

    test("接続が切れ、回答が保存されていなければ理由と「再送信」を出す", async ({ page }) => {
      test.setTimeout(60_000);
      const state = await mockChatPage(page, {
        chunks: [...startedChunks, { afterMs: 600, text: "" }],
        end: "close",
      });
      await send(page);
      const failure = page.getByTestId("chat-send-failure");
      await expect(failure).toContainText("回答を受け取る途中で接続が切れ", { timeout: 20_000 });
      await expect(failure.getByRole("button", { name: "再送信" })).toBeVisible();
      await expect(page.getByTestId("chat-answer-progress-current")).toHaveCount(0);
      expect(state.conversationRequests).toBeGreaterThan(1);
      await expectNoPageOverflow(page);
    });

    test("配信が途絶えたら「接続を確認しています」を出して取り直し、保存済みの回答が出る", async ({
      page,
    }, testInfo) => {
      test.setTimeout(90_000);
      const state = await mockChatPage(page, { chunks: startedChunks, end: "hang" });
      const current = await send(page);
      const reconnecting = page.getByTestId("chat-answer-progress-reconnecting");
      await expect(reconnecting).toHaveText("接続を確認しています。", { timeout: 40_000 });
      // 今の段階の行は残したまま、遅延の案内の代わりに出す。
      await expect(current).toContainText("関係する文書を探しています");
      await expect(current).toHaveAttribute("data-reconnecting", "true");
      await current.scrollIntoViewIfNeeded();
      // 外観の既定はライトなので、ダークは画面の属性でも切り替える（answer-progress.spec.ts と同じ）。
      await page.evaluate((value) => {
        document.documentElement.dataset.theme = value;
      }, colorScheme);
      await page.screenshot({ path: testInfo.outputPath(`chat-reconnecting-${colorScheme}.png`) });
      await expectNoPageOverflow(page);
      // 回答は保存された（配信だけが止まった）。取り直しで回答に置き換わる。
      state.saved = true;
      await expect(page.getByText(savedReply.content).first()).toBeVisible({ timeout: 20_000 });
      await expect(page.getByTestId("chat-live-turn")).toHaveCount(0);
      await expect(reconnecting).toHaveCount(0);
      await expect(page.getByRole("button", { name: "送信" })).toBeVisible();
    });
  });
}
