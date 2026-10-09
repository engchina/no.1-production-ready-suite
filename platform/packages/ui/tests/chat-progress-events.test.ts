import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  CHAT_PROGRESS_SCHEMA_VERSION,
  CHAT_PROGRESS_SSE_EVENT,
  CHAT_PROGRESS_SSE_HEARTBEAT_EVENT,
  CHAT_PROGRESS_STEP_STATUSES,
  CHAT_PROGRESS_TERMINAL_STATUSES,
  chatProgressLabelState,
  chatProgressStepsFromEvents,
  chatProgressTerminalOf,
  parseChatProgressEvent,
  parseChatProgressEvents,
  parseChatProgressPage,
  reduceChatProgressEvents,
  type ChatProgressEvent,
  type ChatProgressEventsState,
  type ChatProgressStepDefinitions,
  type ChatProgressStepEvent,
  type ChatProgressStepStatus,
  type ChatProgressTerminalEvent,
} from "../src";

// #1359: チャットの処理の段階のイベント（3 製品共通の契約）と、イベントを段階の一覧にまとめる reducer。

const here = dirname(fileURLToPath(import.meta.url));
const contract = JSON.parse(
  readFileSync(resolve(here, "../../../contracts/chat-progress/chat-progress-events.json"), "utf-8")
) as {
  schema_version: number;
  absent_fields: string;
  statuses: string[];
  terminal_statuses: string[];
  sse: { event: string; heartbeat_event: string };
  event: { oneOf?: { $ref: string }[]; $defs: Record<string, { properties: Record<string, unknown>; required?: string[] }> };
  page: { properties: Record<string, unknown>; required?: string[] };
};

function step(
  seq: number,
  stepId: string,
  status: ChatProgressStepStatus,
  extra: Partial<ChatProgressStepEvent> = {}
): ChatProgressStepEvent {
  return {
    schema_version: 1,
    type: "step",
    seq,
    target_id: "job-1",
    attempt: 0,
    emitted_at: "2026-10-09T00:00:00.000Z",
    step_id: stepId,
    status,
    ...extra,
  };
}

function terminal(seq: number, status: ChatProgressTerminalEvent["status"] = "done", attempt = 0): ChatProgressTerminalEvent {
  return {
    schema_version: 1,
    type: "terminal",
    seq,
    target_id: "job-1",
    attempt,
    emitted_at: "2026-10-09T00:00:09.000Z",
    status,
  };
}

function reduce(events: ChatProgressEvent[], state: ChatProgressEventsState | null = null, since?: number) {
  return reduceChatProgressEvents(state, { key: "job-1", events, since });
}

const statuses = (state: ChatProgressEventsState) => Object.fromEntries(state.steps.map((s) => [s.id, s.status]));

describe("契約（platform/contracts/chat-progress）", () => {
  it("状態・終端・SSE のイベントの名前・版が backend の契約と同じ", () => {
    expect(contract.schema_version).toBe(CHAT_PROGRESS_SCHEMA_VERSION);
    // 値の無い任意の項目は省く（null を送らない。parse は null も無いものとして扱う）。
    expect(contract.absent_fields).toBe("omitted");
    expect(contract.statuses).toEqual([...CHAT_PROGRESS_STEP_STATUSES]);
    expect(contract.terminal_statuses).toEqual([...CHAT_PROGRESS_TERMINAL_STATUSES]);
    expect(contract.sse.event).toBe(CHAT_PROGRESS_SSE_EVENT);
    expect(contract.sse.heartbeat_event).toBe(CHAT_PROGRESS_SSE_HEARTBEAT_EVENT);
  });

  it("イベントと polling の応答の項目が型と同じ", () => {
    const defs = contract.event.$defs;
    const stepFields: (keyof ChatProgressStepEvent)[] = [
      "schema_version",
      "seq",
      "target_id",
      "attempt",
      "emitted_at",
      "type",
      "step_id",
      "kind",
      "status",
      "started_at",
      "finished_at",
      "detail",
      "params",
    ];
    expect(Object.keys(defs.ChatProgressStepEvent.properties).sort()).toEqual([...stepFields].sort());
    const terminalFields: (keyof ChatProgressTerminalEvent)[] = [
      "schema_version",
      "seq",
      "target_id",
      "attempt",
      "emitted_at",
      "type",
      "status",
    ];
    expect(Object.keys(defs.ChatProgressTerminalEvent.properties).sort()).toEqual([...terminalFields].sort());
    expect(Object.keys(contract.page.properties).sort()).toEqual(["attempt", "events", "last_seq", "target_id", "terminal"]);
  });
});

describe("parseChatProgressEvent", () => {
  it("形の違うイベントは捨てる（SSE・API の応答は未検証の入力）", () => {
    expect(parseChatProgressEvent(step(1, "a", "running"))).toEqual(step(1, "a", "running"));
    expect(parseChatProgressEvent({ ...step(1, "a", "running"), status: "unknown" })).toBeNull();
    expect(parseChatProgressEvent({ ...step(0, "a", "running") })).toBeNull();
    expect(parseChatProgressEvent({ ...step(1, "", "running") })).toBeNull();
    expect(parseChatProgressEvent({ ...terminal(2), status: "skipped" })).toBeNull();
    expect(parseChatProgressEvent("x")).toBeNull();
    expect(parseChatProgressEvents([step(1, "a", "running"), null, { type: "x" }])).toHaveLength(1);
  });

  it("params は文字列・数・真偽の値だけを残す", () => {
    const event = parseChatProgressEvent({ ...step(1, "a", "done"), params: { tool: "rag_search", count: 3, nested: { x: 1 } } });
    expect(event && event.type === "step" ? event.params : null).toEqual({ tool: "rag_search", count: 3 });
  });

  it("polling の応答を検証する", () => {
    expect(parseChatProgressPage({ target_id: "job-1", attempt: 0, events: [step(1, "a", "running")], last_seq: 1, terminal: false })).toEqual({
      target_id: "job-1",
      attempt: 0,
      events: [step(1, "a", "running")],
      last_seq: 1,
      terminal: false,
    });
    expect(parseChatProgressPage({ target_id: "job-1", events: [] })).toBeNull();
  });
});

describe("reduceChatProgressEvents", () => {
  it("seq の順に積み、段階は最初に出た順に並べる", () => {
    const state = reduce([step(1, "queue", "pending"), step(2, "generate", "pending"), step(3, "queue", "running")]);
    expect(state.steps.map((s) => s.id)).toEqual(["queue", "generate"]);
    expect(statuses(state)).toEqual({ queue: "running", generate: "pending" });
    expect(state.lastSeq).toBe(3);
  });

  it("古い・重複のイベントは捨て、変わらなければ同じ状態を返す", () => {
    const first = reduce([step(1, "a", "running"), step(2, "a", "done")]);
    const again = reduce([step(1, "a", "running"), step(2, "a", "done")], first);
    expect(again).toBe(first);
    expect(statuses(reduce([step(1, "a", "pending")], first))).toEqual({ a: "done" });
  });

  it("順序の入れ替わったイベントは、前の番号が届いてから適用する", () => {
    const early = reduce([step(1, "a", "running"), step(3, "b", "running")]);
    expect(statuses(early)).toEqual({ a: "running" });
    expect(early.pending.map((e) => e.seq)).toEqual([3]);
    expect(early.lastSeq).toBe(1);
    const filled = reduce([step(2, "a", "done")], early);
    expect(statuses(filled)).toEqual({ a: "done", b: "running" });
    expect(filled.pending).toEqual([]);
    expect(filled.lastSeq).toBe(3);
  });

  it("polling の応答（since の後をすべて含む）は、記録に無い飛びを越えて進める", () => {
    const early = reduce([step(1, "a", "running"), step(4, "c", "running")]);
    const page = reduce([step(3, "b", "done"), step(4, "c", "running")], early, 1);
    expect(statuses(page)).toEqual({ a: "running", b: "done", c: "running" });
    expect(page.lastSeq).toBe(4);
    expect(page.pending).toEqual([]);
  });

  it("状態は進む方へだけ変え、同じ段階の間の確定（完了 → 失敗）は受け付ける", () => {
    const state = reduce([step(1, "a", "done"), step(2, "a", "running"), step(3, "a", "failed")]);
    expect(statuses(state)).toEqual({ a: "failed" });
  });

  it("開始の時刻は後のイベントに無ければ前の値を残す", () => {
    const state = reduce([
      step(1, "a", "running", { started_at: "2026-10-09T00:00:01.000Z" }),
      step(2, "a", "done", { finished_at: "2026-10-09T00:00:02.000Z", detail: "3 件" }),
    ]);
    expect(state.steps[0]).toMatchObject({ startedAt: "2026-10-09T00:00:01.000Z", finishedAt: "2026-10-09T00:00:02.000Z", detail: "3 件" });
  });

  it("終端の後の、同じ試行の段階のイベントは捨てる", () => {
    const state = reduce([step(1, "a", "running"), step(2, "a", "done"), terminal(3), step(4, "b", "running")]);
    expect(state.terminal).toBe("done");
    expect(statuses(state)).toEqual({ a: "done" });
  });

  it("試行が増えたら作り直し、古い試行のイベントは捨てる", () => {
    const first = reduce([step(1, "a", "done"), step(2, "b", "running")]);
    const retried = reduce([step(3, "a", "running", { attempt: 1 }), step(4, "b", "done", { attempt: 0 })], first);
    expect(retried.attempt).toBe(1);
    expect(statuses(retried)).toEqual({ a: "running" });
  });

  it("対象が変わったら作り直し、別の対象のイベントは捨てる", () => {
    const first = reduce([step(1, "a", "done")]);
    const other = reduceChatProgressEvents(first, { key: "job-2", events: [step(2, "a", "running")] });
    expect(other.key).toBe("job-2");
    expect(other.steps).toEqual([]);
    expect(reduceChatProgressEvents(null, { key: null, events: [step(1, "a", "running")] }).steps).toEqual([]);
  });
});

describe("段階の名前", () => {
  const definitions: ChatProgressStepDefinitions = {
    tool: {
      label: (status, params) => `${String(params.tool)} を${chatProgressLabelState(status)}`,
      detail: (status, params) => (status === "done" && params.count !== undefined ? `${String(params.count)} 件` : undefined),
    },
  };

  it("定義の種類（kind）と値（params）から名前と補足を付け、未知の種類は id を出す", () => {
    const events = [
      step(1, "tool:rag_search", "done", { kind: "tool", params: { tool: "rag_search", count: 2 } }),
      step(2, "unknown", "running", { detail: "補足" }),
      terminal(3, "failed"),
    ];
    expect(chatProgressStepsFromEvents(events, definitions)).toEqual([
      { id: "tool:rag_search", label: "rag_search をdone", status: "done", detail: "2 件" },
      { id: "unknown", label: "unknown", status: "running", detail: "補足" },
    ]);
    expect(chatProgressTerminalOf(events)).toBe("failed");
    expect(chatProgressTerminalOf([])).toBeNull();
  });
});
