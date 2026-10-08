import type { Locator, Page } from "@playwright/test";

import { dbUser } from "./fixtures/auth";
import { MOCK_NOW, expect, test, type MockApi } from "./fixtures/mock-api";

// #1286: チャットの回答の下に、支援タスクの状態（確かめた条件・確認待ちの質問・使った業務ガイド）と
// 回答の検証の結果を業務の言葉で出す。成果物の JSON・内部の語（budget_exceeded など）は業務の利用者に見せない。

const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile-375", width: 375, height: 812 },
];

const THREAD_ID = `thread_${"b".repeat(32)}`;
const GOAL = "経費の精算の手順は？";

/** 画面に出してはいけない内部の語（成果物の JSON の項目名・値）。 */
const INTERNAL_WORDS = [
  "budget_exceeded",
  "budget",
  "user_answer",
  "rag_guide",
  "guide_id",
  "condition_id",
  "no_rag_evidence",
  "validator_unavailable",
  "unvalidated",
  "skipped",
  "support_task",
  "answer_validation",
  "{",
];

async function useTheme(page: Page, theme: "light" | "dark") {
  await page.addInitScript((value) => {
    window.localStorage.setItem("production-ready-agent.ui", JSON.stringify({ state: { theme: value }, version: 0 }));
  }, theme);
}

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth
  );
  expect(overflow).toBeLessThanOrEqual(0);
}

async function expectNoInternalWords(locator: Locator) {
  const text = await locator.innerText();
  for (const word of INTERNAL_WORDS) expect(text, `内部の語「${word}」を出さない`).not.toContain(word);
}

function supportTask(overrides: Record<string, unknown> = {}) {
  return {
    id: "artifact-support",
    kind: "support_task",
    name: "支援タスクの状態",
    created_at: MOCK_NOW,
    content: {
      schema_version: 1,
      thread_id: THREAD_ID,
      owner_user_uuid: "local",
      run_id: "run-review-1",
      goal: GOAL,
      known_conditions: {
        expense_type: {
          value: "出張",
          label: "経費の種類",
          source: "user_answer",
          updated_at: MOCK_NOW,
          previous: [{ value: "交際費", source: "rag_guide", updated_at: MOCK_NOW }],
        },
        amount_band: { value: "5 万円未満", label: "金額", source: "rag_guide", updated_at: MOCK_NOW },
      },
      pending_clarifications: [
        {
          condition_id: "receipt",
          label: "領収書",
          question: "領収書はありますか？",
          options: ["ある", "ない"],
        },
      ],
      guide: { guide_id: "guide-expense", revision: 3, title: "経費精算ガイド", decision: "answer" },
      outcome: "needs_clarification",
      gaps: ["精算の締め日"],
      evidence: [{ document_id: "doc-1", chunk_id: "chunk-1", chunk_set_id: "", file_name: "経費規程.pdf" }],
      budget: {
        run: { tool_calls: 4, rag_calls: 4, tool_seconds: 12.5, rag_seconds: 10, budget_exceeded: 1 },
        task: { runs: 1, tool_calls: 4, rag_calls: 4, tool_seconds: 12.5, rag_seconds: 10 },
        limits: { rag_calls_per_run: 4, tool_calls_per_task: 60 },
      },
      updated_at: MOCK_NOW,
      ...overrides,
    },
  };
}

function validation(content: Record<string, unknown>, id = "artifact-validation") {
  return {
    id,
    kind: "answer_validation",
    name: "回答の検証",
    created_at: MOCK_NOW,
    content: {
      reason: null,
      message: null,
      valid: null,
      connection: "rag",
      tool_name: "rag__rag_validate_answer",
      step_id: "step-validate",
      evidence: [{ document_id: "doc-1", chunk_id: "chunk-1" }],
      result: null,
      connections: [],
      ...content,
    },
  };
}

const WITHHELD = validation({
  status: "completed",
  valid: false,
  result: {
    status: "completed",
    valid: false,
    claims: [
      { answer_quote: "申請は部長の承認が要ります。", status: "supported", reason: "" },
      { answer_quote: "上限は 10 万円です。", status: "contradicted", reason: "規程では 5 万円" },
      { answer_quote: "翌月払いです。", status: "unsupported", reason: "根拠に記載が無い" },
      // 主張ではない段落（出典の行。#1306）は、RAG が確かめていなくても確かめられていない点に出さない。
      { answer_quote: "【経費規程.pdf p.3】", status: "unassessed", reason: "出典の行", non_claim: "citation" },
    ],
    findings: [
      { check: "requests", code: "request_missing", severity: "error", message: "「締め日」の質問に答えていません。" },
      { check: "impact", code: "impact_warning", severity: "warning", message: "出さない警告" },
    ],
    stale_evidence: [{ document_id: "doc-2", chunk_id: "chunk-2" }],
    missing_evidence: [],
  },
  withheld: { claims: 2, findings: 1, all: false },
});

function answer(text: string) {
  return { id: `answer-${text.length}`, kind: "answer", name: "回答", created_at: MOCK_NOW, content: { text } };
}

function seedRun(mockApi: MockApi, id: string, artifacts: unknown[], overrides: Record<string, unknown> = {}) {
  mockApi.state.runs.push({
    id,
    goal: GOAL,
    agent_id: "default",
    runtime_id: "builtin",
    status: "completed",
    steps: [],
    events: [],
    approvals: [],
    artifacts,
    pending_tool_calls: [],
    metadata: {},
    created_by_user_uuid: "local",
    thread_id: THREAD_ID,
    created_at: MOCK_NOW,
    updated_at: MOCK_NOW,
    ...overrides,
  });
}

async function openSeedThread(page: Page) {
  const viewport = page.viewportSize();
  await page.getByTestId("chat-history-toggle").click();
  if (viewport && viewport.width >= 1024) {
    await page.getByTestId("chat-history").getByRole("button", { name: new RegExp(GOAL) }).click();
    return;
  }
  await page.getByRole("dialog", { name: "会話の履歴" }).getByText(GOAL).click();
}

for (const viewport of VIEWPORTS) {
  for (const theme of ["light", "dark"] as const) {
    test(`回答の下に確かめた条件・確認待ち・業務ガイド・確かめられていない点を業務の言葉で出す (${viewport.name}, ${theme})`, async ({ page, mockApi }, testInfo) => {
      seedRun(mockApi, "run-review-1", [answer("精算は申請から始めます。"), supportTask(), WITHHELD]);
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await useTheme(page, theme);
      await page.goto("/chat");
      await openSeedThread(page);

      const turn = page.getByTestId("chat-turn-run-review-1");
      await expect(turn.getByText("精算は申請から始めます。")).toBeVisible();
      // 予算の上限は控えめな案内だけ（内部の語は出さない）。
      await expect(turn.getByTestId("chat-review-run-review-1-limit")).toContainText(
        "資料を調べられる回数の上限に達したため、ここまでに集めた資料で答えています。"
      );
      // 畳んでいても、資料で確かめた結果は見出しのバッジで分かる（アイコン付き）。
      const summary = turn.getByTestId("chat-review-run-review-1-summary");
      await expect(summary).toContainText("回答の確かめ");
      const badge = summary.locator("[data-status-variant]");
      await expect(badge).toHaveText("一部を確かめられず省略 2 件");
      await expect(badge).toHaveAttribute("data-status-variant", "warning");
      await expect(badge.locator("svg")).toHaveCount(1);
      const panel = turn.getByTestId("chat-review-run-review-1");
      await expect(panel).not.toHaveAttribute("open", "");

      await summary.click();
      await expect(panel).toHaveAttribute("open", "");
      const conditions = panel.getByRole("group", { name: "確かめた条件" });
      await expect(conditions.getByRole("listitem")).toHaveText([
        "経費の種類：出張（あなたの答えから）前の値：交際費",
        "金額：5 万円未満（質問の文から）",
      ]);
      const clarifications = panel.getByRole("group", { name: "確認待ちの質問" });
      await expect(clarifications).toContainText("領収書はありますか？");
      await expect(clarifications).toContainText("選択肢：ある / ない");
      await expect(panel.getByRole("group", { name: "使った業務ガイド" })).toHaveText(
        "使った業務ガイド経費精算ガイド（第 3 版）"
      );
      await expect(panel.getByRole("group", { name: "まだ足りない情報" })).toContainText("精算の締め日");
      const result = panel.getByRole("group", { name: "資料で確かめた結果" });
      await expect(result).toContainText("資料で確かめられなかった 2 件の内容は、回答に載せていません。");
      await expect(result.getByTestId("answer-review-unverified").getByRole("listitem")).toHaveText([
        "資料と食い違う：「上限は 10 万円です。」（規程では 5 万円）",
        "資料で確かめられない：「翌月払いです。」（根拠に記載が無い）",
        "「締め日」の質問に答えていません。",
        "使った資料のうち 1 件は古い版です。最新の版で確かめ直してください。",
      ]);
      // warning の指摘・裏付けのある段落は出さない。
      await expect(result).not.toContainText("出さない警告");
      await expect(result).not.toContainText("申請は部長の承認が要ります。");
      await expectNoInternalWords(turn);

      await expectNoHorizontalOverflow(page);
      await page.screenshot({ path: testInfo.outputPath(`review-${viewport.name}-${theme}.png`), fullPage: true });
    });
  }
}

test("資料で確かめた結果は、検証済み・本文を省略・検証できなかった・対象外を見分けて出す", async ({ page, mockApi }) => {
  seedRun(mockApi, "run-verified", [
    answer("回答 1"),
    validation({ status: "completed", valid: true, result: { status: "completed", valid: true, claims: [] }, withheld: { claims: 0, findings: 0, all: false } }),
  ]);
  seedRun(mockApi, "run-all", [
    answer("回答 2"),
    validation({
      status: "completed",
      valid: false,
      result: { status: "no_evidence", valid: false, claims: [], findings: [] },
      withheld: { claims: 0, findings: 0, all: true },
    }),
  ]);
  seedRun(mockApi, "run-failed", [
    answer("回答 3"),
    validation({ status: "unvalidated", reason: "validator_unavailable", message: "接続できません" }),
  ]);
  seedRun(mockApi, "run-no-evidence", [answer("回答 4"), validation({ status: "unvalidated", reason: "no_rag_evidence" })]);
  seedRun(mockApi, "run-skipped", [answer("回答 5"), validation({ status: "skipped", reason: "no_rag_evidence" })]);
  // 資料に答えが無い質問の拒答（「記載がありません」の文は主張ではない。#1306）。
  seedRun(mockApi, "run-refusal", [
    answer("回答 6"),
    validation({
      status: "completed",
      valid: false,
      result: {
        status: "completed",
        valid: false,
        claims: [
          { answer_quote: "資料に記載がありません。", status: "unsupported", reason: "", non_claim: "absence" },
        ],
        findings: [],
        stale_evidence: [],
        missing_evidence: [],
      },
      withheld: { claims: 0, findings: 0, all: false },
    }),
  ]);
  seedRun(mockApi, "run-clarification", [
    answer("回答 7"),
    validation({ status: "skipped", reason: "clarification_only" }),
  ]);
  await page.goto("/chat");
  await openSeedThread(page);

  const cases = [
    ["run-verified", "検証済み", "success", "回答の内容を、使った資料と照らし合わせて確かめました。"],
    ["run-all", "確かめられず本文を省略", "warning", "資料で確かめられる内容が無かったため、回答の本文は載せていません。"],
    ["run-failed", "検証できませんでした", "warning", "資料との照らし合わせが途中で止まったため、この回答は確かめられていません。"],
    ["run-no-evidence", "検証できませんでした", "warning", "この回答は資料を使わずに作ったため、資料と照らし合わせて確かめていません。"],
    ["run-skipped", "検証の対象外", "neutral", "この業務 Agent は資料を使わないため、資料との照らし合わせの対象外です。"],
    ["run-refusal", "検証済み", "success", "回答の内容を、使った資料と照らし合わせて確かめました。"],
    ["run-clarification", "検証の対象外", "neutral", "確認の質問だけの回答のため、資料との照らし合わせの対象外です。"],
  ] as const;
  for (const [runId, label, variant, message] of cases) {
    const turn = page.getByTestId(`chat-turn-${runId}`);
    const badge = turn.getByTestId(`chat-review-${runId}-summary`).locator("[data-status-variant]");
    await expect(badge).toHaveText(label);
    await expect(badge).toHaveAttribute("data-status-variant", variant);
    await turn.getByTestId(`chat-review-${runId}-summary`).click();
    await expect(turn.getByRole("group", { name: "資料で確かめた結果" })).toContainText(message);
    // 条件・確認待ちが無い回答は、その見出しを出さない。予算の案内も出さない。
    await expect(turn.getByRole("group", { name: "確かめた条件" })).toHaveCount(0);
    await expect(turn.getByTestId(`chat-review-${runId}-limit`)).toHaveCount(0);
    await expectNoInternalWords(turn);
  }
});

test("支援タスクの状態も回答の検証も無い回答には、回答の確かめを出さない", async ({ page, mockApi }) => {
  seedRun(mockApi, "run-plain", [answer("状態の無い回答")]);
  // 作成中の Run は、前の状態が残っていても出さない（回答が出てから出す）。
  seedRun(mockApi, "run-active", [supportTask()], { status: "running" });
  await page.goto("/chat");
  await openSeedThread(page);

  await expect(page.getByTestId("chat-turn-run-plain").getByText("状態の無い回答")).toBeVisible();
  await expect(page.getByTestId("chat-review-run-plain")).toHaveCount(0);
  await expect(page.getByTestId("chat-review-run-plain-limit")).toHaveCount(0);
  await expect(page.getByTestId("chat-review-run-active")).toHaveCount(0);
  await expect(page.getByTestId("chat-review-run-active-limit")).toHaveCount(0);
});

test("確認待ちの質問だけがある回答は、見出しにバッジを出さずに確認待ちの質問を出す", async ({ page, mockApi }) => {
  seedRun(mockApi, "run-clarify", [
    answer("領収書はありますか？"),
    supportTask({
      known_conditions: {},
      guide: null,
      gaps: [],
      budget: { run: { budget_exceeded: 0 }, task: {}, limits: {} },
    }),
  ]);
  await page.goto("/chat");
  await openSeedThread(page);

  const summary = page.getByTestId("chat-review-run-clarify-summary");
  await expect(summary.locator("[data-status-variant]")).toHaveCount(0);
  await summary.click();
  const panel = page.getByTestId("chat-review-run-clarify");
  await expect(panel.getByRole("group", { name: "確認待ちの質問" })).toContainText("領収書はありますか？");
  await expect(panel.getByRole("group", { name: "確かめた条件" })).toHaveCount(0);
  await expect(panel.getByRole("group", { name: "資料で確かめた結果" })).toHaveCount(0);
  await expect(page.getByTestId("chat-review-run-clarify-limit")).toHaveCount(0);
});

test("実行履歴の詳細では業務の言葉で出し、元の JSON は管理者だけに出す", async ({ page, mockApi }) => {
  seedRun(mockApi, "run-review-1", [answer("精算は申請から始めます。"), supportTask(), WITHHELD]);
  await page.goto("/runs?id=run-review-1");

  const support = page.getByTestId("run-review-artifact-support");
  await expect(support.getByRole("group", { name: "確かめた条件" })).toContainText("経費の種類：出張");
  await expect(page.getByTestId("run-review-limit-artifact-support")).toBeVisible();
  const validationArtifact = page.getByTestId("run-review-artifact-validation");
  await expect(validationArtifact.getByRole("group", { name: "資料で確かめた結果" })).toContainText(
    "一部を確かめられず省略 2 件"
  );
  // 管理者（既定のローカル利用者）は元の JSON を畳んで見られる。
  await expect(support.getByText("元の JSON")).toBeVisible();

  // 実行の閲覧だけの利用者には元の JSON を出さない。
  mockApi.setCurrentUser(
    dbUser({ login_user_id: "viewer.user", permissions: ["agent.runs.view"], allowed_agent_ids: ["default"] })
  );
  await page.reload();
  await expect(support.getByRole("group", { name: "確かめた条件" })).toContainText("経費の種類：出張");
  await expect(support.getByText("元の JSON")).toHaveCount(0);
  await expectNoInternalWords(support);
  await expectNoInternalWords(validationArtifact);
});
