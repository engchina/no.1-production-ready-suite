import { expect, test, type Page } from "./_helpers/test";
import { mockDatabaseGateReady, systemAdminMe } from "./_helpers/database-gate";
import { confirmChatRequirement, mockChatRequirement } from "./_helpers/chat-requirement";
import { withJobProgress } from "./_helpers/job-progress";

/**
 * 「AI 活用」の下の機能のデータの見え方（#1889・#1928）。system_admin は全員の会話を見られ、履歴に利用者の
 * 表示名を出す。他の利用者の会話でも本人と同じく続きの質問を送れ、持ち主以外が送った質問には送った人の
 * 表示名を添える。一般の利用者の履歴は本人の会話だけで、利用者を出さない（絞り込みは backend。ここでは
 * 画面の出し方を確かめる）。
 */

const profile = {
  id: "sales",
  name: "売上分析",
  description: "売上の集計",
  archived: false,
  allowed_tables: ["APP.SALES"],
  allowed_views: [],
  allowed_table_count: 1,
  allowed_view_count: 0,
  version: 1,
};
const now = "2026-10-07T09:59:00Z";
const SQL = "SELECT * FROM APP.SALES";

type Conversation = {
  id: string;
  title: string;
  profile_id: string;
  created_at: string;
  owned: boolean;
  actor_display_name: string;
};

function turn(jobId: string, question: string, sender: string | null) {
  return {
    job_id: jobId,
    question,
    conversation_id: "chat-alice",
    chat: true,
    generation_only: true,
    status: "done",
    created_at: now,
    steps: [],
    last_execution: null,
    sender_display_name: sender,
    result: {
      generated_sql: SQL,
      original_question: question,
      explanation: "売上の明細です。",
      safety: { is_safe: true },
    },
  };
}

async function setup(
  page: Page,
  conversations: Conversation[],
  turns: Record<string, ReturnType<typeof turn>[]> = {},
) {
  await mockDatabaseGateReady(page);
  await mockChatRequirement(page);
  const jobRequests: Record<string, unknown>[] = [];
  await page.route("**/api/nl2sql/profiles/search**", (route) =>
    route.fulfill({ json: { data: { items: [profile], total: 1, next_cursor: null } } }),
  );
  await page.route("**/api/nl2sql/profiles/*/usage-context", (route) =>
    route.fulfill({ json: { data: profile } }),
  );
  await page.route("**/api/nl2sql/chats**", (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/nl2sql/chats") {
      return route.fulfill({
        json: {
          data: { items: conversations, next_cursor: null, total: conversations.length, limit: 10 },
        },
      });
    }
    const id = decodeURIComponent(url.pathname.split("/").at(-1) ?? "");
    const target = conversations.find((item) => item.id === id);
    if (!target) {
      return route.fulfill({ status: 404, json: { error_messages: ["会話が見つかりません。"] } });
    }
    return route.fulfill({
      json: { data: { conversation: target, turns: (turns[id] ?? []).map(withJobProgress) } },
    });
  });
  // system_admin が他の利用者の会話に送った続き（#1928）。backend は送った人を送り手として返す。
  await page.route("**/api/nl2sql/jobs", (route) => {
    const body = route.request().postDataJSON();
    jobRequests.push(body);
    const list = (turns["chat-alice"] ??= []);
    const sent = turn(`chat-${list.length + 1}`, body.question, "システム管理者");
    list.push(sent);
    return route.fulfill({
      json: {
        data: {
          job_id: sent.job_id,
          status: "done",
          created_at: now,
          steps: [],
          progress_events: withJobProgress(sent).progress_events,
        },
      },
    });
  });
  return { jobRequests };
}

async function openHistory(page: Page) {
  await page.goto("/chat");
  const toggle = page.getByTestId("sql-chat-history-toggle");
  await expect(toggle).toBeEnabled();
  await toggle.click();
  return page.getByTestId("sql-chat-history");
}

test.describe("会話のデータの見え方（#1889・#1928）", { tag: "@responsive" }, () => {
  test("system_admin は全員の会話を利用者つきで見て、他の利用者の会話に続きを送れる", async ({ page }) => {
    const { jobRequests } = await setup(
      page,
      [
        {
          id: "chat-alice",
          title: "アリスの売上",
          profile_id: "sales",
          created_at: now,
          owned: false,
          actor_display_name: "アリス",
        },
        {
          id: "chat-gone",
          title: "削除済みの人の会話",
          profile_id: "sales",
          created_at: "2026-10-07T09:58:00Z",
          owned: false,
          actor_display_name: "",
        },
      ],
      {
        "chat-alice": [
          turn("chat-alice", "売上の明細", null),
          turn("chat-2", "カテゴリ別にして", "別の管理者"),
          turn("chat-3", "地域別にして", ""),
        ],
      },
    );
    const history = await openHistory(page);
    await expect(history.getByText(/・アリス$/)).toBeVisible();
    await expect(history.getByText(/・（削除済みのユーザー）$/)).toBeVisible();

    await history.locator('[title="アリスの売上"]').click();
    const turns = page.getByTestId("sql-chat-turn");
    await expect(turns).toHaveCount(3);
    // 持ち主の質問には送り手を出さず、持ち主以外が送った質問には送った人を添える（引けない人は削除済み）。
    await expect(turns.nth(0).locator("[data-chat-sender]")).toHaveCount(0);
    await expect(turns.nth(1).locator("[data-chat-sender]")).toHaveText("別の管理者 が送信");
    await expect(turns.nth(2).locator("[data-chat-sender]")).toHaveText(
      "（削除済みのユーザー） が送信",
    );
    // 見るだけにする案内は出さず、入力欄と送信は使える。
    await expect(page.getByText(/ほかの利用者の会話です。/)).toHaveCount(0);
    const composer = page.getByRole("textbox", { name: "質問", exact: true });
    await composer.fill("上位 5 件だけ");
    await expect(page.getByTestId("sql-chat-send")).toBeEnabled();
    await composer.press("Enter");
    await confirmChatRequirement(page);
    await expect(turns).toHaveCount(4);
    expect(jobRequests[0]).toMatchObject({
      question: "上位 5 件だけ",
      chat: true,
      previous_job_id: "chat-3",
    });
    const sent = turns.nth(3);
    await expect(sent.locator("[data-chat-sender]")).toHaveText("システム管理者 が送信");
    // 送り手は吹き出しの上（右寄せの同じ列）に出す。
    const senderBox = await sent.locator("[data-chat-sender]").boundingBox();
    const bubbleBox = await sent.getByText("上位 5 件だけ", { exact: true }).first().boundingBox();
    expect(senderBox && bubbleBox && senderBox.y + senderBox.height <= bubbleBox.y).toBe(true);

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });

  test("一般の利用者の履歴は本人の会話だけで、利用者を出さない", async ({ page }) => {
    await setup(
      page,
      [
        {
          id: "chat-own",
          title: "自分の売上",
          profile_id: "sales",
          created_at: now,
          owned: true,
          actor_display_name: "",
        },
      ],
      { "chat-own": [turn("chat-own", "自分の売上", null)] },
    );
    // setup の後に登録した auth/me が優先されるよう、一般の利用者を登録し直す。
    await page.route("**/api/auth/me", (route) =>
      route.fulfill({
        json: {
          data: {
            ...systemAdminMe,
            is_system_admin: false,
            role_codes: ["ANALYST"],
            permissions: ["menu.chat", "nl2sql.query.generate", "nl2sql.sql.execute"],
            allowed_profile_ids: ["sales"],
          },
        },
      }),
    );
    const history = await openHistory(page);
    await expect(history.locator('[title="自分の売上"]')).toBeVisible();
    await expect(history.getByText(/・/)).toHaveCount(0);
    await history.locator('[title="自分の売上"]').click();
    await expect(page.getByTestId("sql-chat-turn")).toHaveCount(1);
    await expect(page.locator("[data-chat-sender]")).toHaveCount(0);
  });
});
