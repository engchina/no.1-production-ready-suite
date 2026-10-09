/**
 * チャットの処理の段階（3 製品共通の ChatProgressStep。#1147）の、Agent の段階の定義の確認（#1359）。
 *
 * 段階は backend が Run の状態から記録したイベント（`progress_events`）で届く。画面は段階の種類（`kind`）から
 * i18n の名前と補足を付けるだけなので、backend と同じ形のイベントから名前を付けた一覧を確かめる。
 * Agent の frontend は Vitest を持たないため、画面を開かずに Playwright の test で関数を確かめる。
 */
import { chatProgressStepsFromEvents, chatProgressTerminalOf, parseChatProgressEvents, type ChatProgressStep } from "@production-ready/ui";

import { AGENT_CHAT_PROGRESS_STEPS, chatSubmitProgressSteps } from "../src/lib/chat-progress";

import { ProgressLog } from "./fixtures/chat-progress-events";
import { expect, test } from "./fixtures/test";

const T0 = "2026-10-04T12:00:00.000Z";

function steps(log: ProgressLog): ChatProgressStep[] {
  return chatProgressStepsFromEvents(parseChatProgressEvents(log.events), AGENT_CHAT_PROGRESS_STEPS);
}

function summary(items: ChatProgressStep[]): string[] {
  return items.map((step) => `${step.id}:${step.status}`);
}

function newLog(): ProgressLog {
  return new ProgressLog("run-1", () => T0);
}

// 画面幅に関係しないので、題名の「（desktop）」で mobile-375 の project では実行しない（playwright.config.ts の grepInvert）。
test.describe("Agent の処理の段階の定義（desktop）", () => {
  test("送信中は質問の送信の段階だけを出す", () => {
    const [submit] = chatSubmitProgressSteps(Date.parse(T0));
    expect(submit).toMatchObject({ id: "submit", label: "質問を送信しています", status: "running", startedAt: T0 });
  });

  test("考えている段階の名前は状態で変わる", () => {
    const log = newLog().step("plan", "running");
    expect(steps(log)[0]).toMatchObject({ label: "考えています", startedAt: T0 });
    log.step("plan", "done");
    expect(steps(log)[0].label).toBe("進め方を決めました");
  });

  test("ツールの段階は params のツール名で名前を付け、同じツールの 2 回目は id で分ける", () => {
    const tool = { params: { tool: "rag__rag_search" } };
    const log = newLog()
      .step("plan", "done")
      .step("tool:rag__rag_search", "done", tool)
      .step("respond", "done", { kind: "plan" })
      .step("tool:rag__rag_search#2", "running", tool);
    const items = steps(log);
    expect(summary(items)).toEqual(["plan:done", "tool:rag__rag_search:done", "respond:done", "tool:rag__rag_search#2:running"]);
    expect(items[1].label).toBe("ツール rag__rag_search を呼びました");
    // 回答の作成の後に別のツールを呼んだ段階は、backend が進め方の検討（kind plan）に変える。
    expect(items[2].label).toBe("進め方を決めました");
    expect(items[3].label).toBe("ツール rag__rag_search を呼んでいます");
  });

  test("承認待ちは待っている間だけツール名を補足に出し、待機中のツールは名詞で出す", () => {
    const log = newLog()
      .step("plan", "done")
      .step("approval_wait", "running", { params: { tools: "nl2sql__execute" } })
      .step("tool:nl2sql__execute", "pending", { params: { tool: "nl2sql__execute" } });
    let items = steps(log);
    expect(summary(items)).toEqual(["plan:done", "approval_wait:running", "tool:nl2sql__execute:pending"]);
    expect(items[1]).toMatchObject({ label: "承認を待っています", detail: "nl2sql__execute" });
    expect(items[2]).toMatchObject({ label: "ツール nl2sql__execute の呼び出し" });
    expect(items[2].startedAt).toBeUndefined();

    log.step("approval_wait", "done");
    items = steps(log);
    expect(items[1]).toMatchObject({ label: "承認の判断を受け取りました" });
    expect(items[1].detail).toBeUndefined();
  });

  test("却下したツールはスキップと補足を出す", () => {
    const log = newLog()
      .step("plan", "done")
      .step("approval_wait", "done", { params: { tools: "nl2sql__execute" } })
      .step("tool:nl2sql__execute", "skipped", { params: { tool: "nl2sql__execute", rejected: true } })
      .step("respond", "done")
      .terminal("done");
    const items = steps(log);
    expect(summary(items)).toEqual(["plan:done", "approval_wait:done", "tool:nl2sql__execute:skipped", "respond:done"]);
    expect(items[2].detail).toBe("承認されませんでした");
    expect(chatProgressTerminalOf(parseChatProgressEvents(log.events))).toBe("done");
  });

  test("回答の作成の 2 回目と失敗の名前", () => {
    const log = newLog().step("plan", "done").step("respond#2", "running");
    expect(steps(log)[1]).toMatchObject({ id: "respond#2", label: "回答を作っています" });
    log.step("respond#2", "failed").terminal("failed");
    expect(steps(log)[1]).toMatchObject({ label: "回答を作れませんでした", status: "failed" });
    expect(chatProgressTerminalOf(parseChatProgressEvents(log.events))).toBe("failed");
  });
});
