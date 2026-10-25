/**
 * チャットの処理の経過（3 製品共通の ChatProgressStep。#1147）の、Agent の工程の定義の確認（#1359）。
 *
 * 処理の経過は Run の trace（span の木。#3226）の投影で、共通の span の表の snapshot（`GET /api/runs/{id}/trace`）で
 * 届く。画面は span の親子から木にし、工程の span の種類（`kind`）から i18n の名前と補足を付けるので、backend と同じ
 * 形のイベントから名前を付けた一覧を確かめる。画面を開かずに Playwright の test で関数を確かめる。
 */
import { chatProgressStepsFromEvents, chatProgressTerminalOf, parseChatProgressEvents, type ChatProgressStep } from "@production-ready/ui";

import { AGENT_CHAT_PROGRESS_STEPS, chatSubmitProgressSteps } from "../src/lib/chat-progress";

import { EXECUTE_SPAN, ProgressLog, SYNTHETIC_MODEL } from "./fixtures/chat-progress-events";
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
test.describe("Agent の処理の経過の定義（desktop）", () => {
  test("送信中は質問の送信の段階だけを出す", () => {
    const [submit] = chatSubmitProgressSteps(Date.parse(T0));
    expect(submit).toMatchObject({ id: "submit", label: "質問を送信しています", status: "running", startedAt: T0 });
  });

  test("実行の全体の span は行にせず、呼び出し中のモデルは共通の名前、終わったら工程の名前で出す", () => {
    const log = newLog().step("chat-1", "running");
    let items = steps(log);
    expect(summary(items)).toEqual(["chat-1:running"]);
    expect(items.map((step) => step.id)).not.toContain(EXECUTE_SPAN);
    expect(items[0]).toMatchObject({ label: `モデルの呼び出し · ${SYNTHETIC_MODEL}`, startedAt: T0 });
    log.step("chat-1", "done", { kind: "plan" });
    items = steps(log);
    expect(items[0].label).toBe("進め方を決めました");
    // 行を開くと span の技術の値（Span name・モデル）を出す。
    expect(items[0].details?.fields).toEqual(
      expect.arrayContaining([
        { label: "Span name", value: "gen_ai.chat", mono: true },
        { label: "gen_ai.request.model", value: SYNTHETIC_MODEL, mono: true },
      ])
    );
  });

  test("表示名の無い処理は内部名を出さず、ツールの中の MCP の呼び出しは tools/call だけを入れ子にする", () => {
    // 1 回のツールの呼び出しは initialize・通知・tools/call の 3 つの MCP の要求になる。手続きの要求は行にしない（#3272）。
    const mcp = (method: string, tool?: string) => ({
      parent: "tool:rag__rag_search",
      attributes: { "mcp.method.name": method, ...(tool ? { "gen_ai.tool.name": tool } : {}) },
    });
    const log = newLog()
      .step("chat-1", "done", { kind: "plan" })
      .step("tool:rag__rag_search", "running")
      .step("mcp-init", "done", mcp("initialize"))
      .step("mcp-notify", "done", mcp("notifications/initialized"))
      .step("mcp-1", "done", mcp("tools/call", "rag_search"))
      .step("tool:rag__rag_search", "done")
      .step("chat-2", "running");
    const items = steps(log);
    expect(summary(items)).toEqual(["chat-1:done", "tool:rag__rag_search:done", "mcp-1:done", "chat-2:running"]);
    expect(items[1].label).toBe("処理を実行しました");
    expect(items[2]).toMatchObject({ label: "MCP の呼び出し · rag_search", level: 1 });
    expect(items[3].label).toBe(`モデルの呼び出し · ${SYNTHETIC_MODEL}`);
  });

  test("承認の待ちは、再開の区間で待ったツール名と一緒に出す", () => {
    const log = newLog().step("chat-1", "done", { kind: "plan" }).pause();
    // 待っている間は待ちの行を出さない（見出しと承認のカードで示す）。
    expect(summary(steps(log))).toEqual(["chat-1:done"]);

    log
      .resume()
      .step("approval_wait", "done", { params: { tools: "nl2sql__execute", round: 1 } })
      .step("tool:nl2sql__execute", "done")
      .step("chat-2", "done", { kind: "respond" })
      .terminal("done");
    const items = steps(log);
    expect(summary(items)).toEqual(["chat-1:done", "approval_wait:done", "tool:nl2sql__execute:done", "chat-2:done"]);
    expect(items[3].label).toBe("回答を作りました");
    expect(items[1]).toMatchObject({ label: "承認の判断を受け取りました", detail: "nl2sql__execute" });
    expect(items.every((step) => !step.level)).toBe(true);
    expect(chatProgressTerminalOf(parseChatProgressEvents(log.events))).toBe("done");
  });

  test("却下したツールはスキップと補足を出す", () => {
    const log = newLog()
      .step("chat-1", "done", { kind: "plan" })
      .pause()
      .resume()
      .step("approval_wait", "done", { params: { tools: "nl2sql__execute", round: 1 } })
      .step("tool:nl2sql__execute", "skipped", { params: { tool: "nl2sql__execute", rejected: true } })
      .terminal("done");
    const items = steps(log);
    expect(summary(items)).toEqual(["chat-1:done", "approval_wait:done", "tool:nl2sql__execute:skipped"]);
    expect(items[2].detail).toBe("承認されませんでした");
  });

  test("失敗したモデルの呼び出しは共通の名前で失敗を出す", () => {
    const log = newLog().step("chat-1", "done", { kind: "plan" }).step("chat-2", "running");
    log.step("chat-2", "failed").terminal("failed");
    expect(steps(log)[1]).toMatchObject({ label: `モデルの呼び出し · ${SYNTHETIC_MODEL}`, status: "failed" });
    expect(chatProgressTerminalOf(parseChatProgressEvents(log.events))).toBe("failed");
  });

  test("停止は開いていた span をスキップで閉じる", () => {
    const log = newLog().step("chat-1", "done", { kind: "plan" }).step("tool:rag__rag_search", "running").terminal("cancelled");
    expect(summary(steps(log))).toEqual(["chat-1:done", "tool:rag__rag_search:skipped"]);
    expect(chatProgressTerminalOf(parseChatProgressEvents(log.events))).toBe("cancelled");
  });
});
