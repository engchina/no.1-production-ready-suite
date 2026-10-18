import { describe, expect, it } from "vitest";

import type { RunState } from "@/lib/api";
import { AGENT_CHAT_PROGRESS_STEPS } from "@/lib/chat-progress";
import {
  DEFAULT_AGENT_RUN_KIND,
  backendRunExecutor,
  backendRunKind,
  backendRunStatus,
  commonRunStatus,
  runDurationMs,
  runFinishedAt,
  runStatusLabel,
  pairToolProgressSteps,
  runTimelineSteps,
  runToolTitles,
  toolStepDetails,
  withToolStepDetails,
} from "@/lib/run-history";

const NOW = "2026-10-15T09:00:00.000Z";

function run(overrides: Partial<RunState> = {}): RunState {
  return {
    id: "run-1",
    goal: "売上を調べる",
    agent_id: "default",
    runtime_id: "builtin",
    status: "completed",
    steps: [],
    events: [],
    approvals: [],
    artifacts: [],
    pending_tool_calls: [],
    metadata: {},
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  } as RunState;
}

// 実行履歴の共通の型（#1955）。
describe("実行履歴の状態の写し（run-history.md「状態の写し方」）", () => {
  it("Agent の状態を共通の 7 つに写し、承認待ち・確認待ちだけ Agent の言い方で上書きする", () => {
    expect(commonRunStatus("waiting_approval")).toBe("waiting");
    expect(commonRunStatus("waiting_confirmation")).toBe("waiting");
    expect(commonRunStatus("completed")).toBe("succeeded");
    expect(commonRunStatus("cancelled")).toBe("cancelled");
    expect(runStatusLabel("waiting_approval")).toBe("承認待ち");
    expect(runStatusLabel("waiting_confirmation")).toBe("確認待ち");
    expect(runStatusLabel("completed")).toBeUndefined();
  });

  it("共通の絞り込みの値を backend の条件に写す", () => {
    expect(backendRunStatus("all")).toBeUndefined();
    expect(backendRunStatus("succeeded")).toBe("completed");
    expect(backendRunStatus("waiting")).toBe("waiting");
    expect(backendRunExecutor("self")).toBe("mine");
    expect(backendRunExecutor("others")).toBe("others");
    expect(backendRunExecutor("all")).toBeUndefined();
    // 種類の既定は品質評価を除く（#1983）。「すべて」は送らない。
    expect(DEFAULT_AGENT_RUN_KIND).toBe("not_evaluation");
    expect(backendRunKind(DEFAULT_AGENT_RUN_KIND)).toBe("not_evaluation");
    expect(backendRunKind("all")).toBeUndefined();
    expect(backendRunKind("evaluation")).toBe("evaluation");
  });
});

describe("詳細の時間と処理の経過", () => {
  it("終わった実行だけ終了日時と所要時間を持つ（終わりの Event の時刻）", () => {
    const done = run({
      events: [
        { id: "e1", run_id: "run-1", type: "run.completed", message: "", payload: {}, created_at: "2026-10-15T09:00:03.200Z" },
      ],
    });
    expect(runFinishedAt(done)).toBe("2026-10-15T09:00:03.200Z");
    expect(runDurationMs(done)).toBe(3200);
    expect(runFinishedAt(run({ status: "running" }))).toBeNull();
    expect(runDurationMs(run({ status: "waiting_approval" }))).toBeNull();
  });

  it("段の記録の無い Run は step をチャットと同じ名前のツールの段にする", () => {
    const steps = runTimelineSteps(
      run({
        steps: [
          {
            id: "s1",
            run_id: "run-1",
            kind: "tool",
            status: "failed",
            tool_call: { name: "rag__rag_search", arguments: {} },
          } as RunState["steps"][number],
        ],
      }),
      () => "中身"
    );
    expect(steps).toHaveLength(1);
    expect(steps[0]).toMatchObject({ id: "step:s1", status: "failed", label: "ツール rag__rag_search を呼べませんでした" });
    expect(steps[0].children).toBe("中身");
  });

  // #2324: ツールの表示名（MCP の title。metadata `tool_titles`）があれば段の名前に出す。
  it("ツールの表示名があれば段の名前を表示名にし、無ければ内部名に戻る", () => {
    const titled = run({
      metadata: { tool_titles: { rag__rag_search: "ナレッジベースの検索", bad: 1 } },
      steps: [
        { id: "s1", run_id: "run-1", kind: "tool", status: "completed", tool_call: { name: "rag__rag_search", arguments: {} } },
        { id: "s2", run_id: "run-1", kind: "tool", status: "completed", tool_call: { name: "ext__other", arguments: {} } },
      ] as RunState["steps"],
    });
    expect(runToolTitles(titled)).toEqual({ rag__rag_search: "ナレッジベースの検索" });
    expect(runToolTitles(run({ metadata: { tool_titles: ["x"] } }))).toEqual({});
    expect(runTimelineSteps(titled, () => undefined).map((step) => step.label)).toEqual([
      "「ナレッジベースの検索」を実行しました",
      "ツール ext__other を呼びました",
    ]);
    const label = AGENT_CHAT_PROGRESS_STEPS.tool.label;
    expect(label("running", { tool: "rag__rag_search", title: "ナレッジベースの検索" })).toBe(
      "「ナレッジベースの検索」を実行しています"
    );
    expect(label("failed", { tool: "rag__rag_search", title: " " })).toBe("ツール rag__rag_search を呼べませんでした");
  });
});

// #2294: チャットの処理の経過のツールの段を開いて見る詳細。
describe("チャットの処理の経過のツールの段の詳細", () => {
  const toolStep = (id: string, name: string, extra: Partial<RunState["steps"][number]> = {}) =>
    ({
      id,
      run_id: "run-1",
      kind: "tool",
      status: "completed",
      tool_call: { name, arguments: { query: "条件 A" } },
      ...extra,
    }) as RunState["steps"][number];

  it("同じツールの n 回目の段を n 回目の step に結び、引数と結果の JSON・エラーを詳細にする", () => {
    const progress = [
      { id: "plan", label: "進め方を決めました", status: "done" as const },
      { id: "tool:rag__rag_search", label: "ツール rag__rag_search を呼びました", status: "done" as const },
      { id: "tool:rag__rag_search#2", label: "ツール rag__rag_search を呼べませんでした", status: "failed" as const },
    ];
    const steps = [
      toolStep("s1", "rag__rag_search", {
        tool_result: { name: "rag__rag_search", success: true, output: { answer: "回答 A" } } as never,
      }),
      toolStep("s2", "rag__rag_search", {
        status: "failed",
        tool_result: { name: "rag__rag_search", success: false, error: "時間切れ" } as never,
      }),
    ];
    const [plan, first, second] = withToolStepDetails(progress, steps);
    expect(plan.details).toBeUndefined();
    expect(first.details?.code?.map((block) => [block.label, block.language])).toEqual([
      ["引数", "json"],
      ["結果", "json"],
    ]);
    expect(first.details?.code?.[1].text).toContain("回答 A");
    // 段の名前は表示名のことがあるので、内部名を詳細の先頭に出す（#2324）。
    expect(first.details?.fields).toEqual([{ label: "ツール", value: "rag__rag_search", mono: true }]);
    expect(second.details?.fields).toEqual([
      { label: "ツール", value: "rag__rag_search", mono: true },
      { label: "エラー", value: "時間切れ" },
    ]);
    expect(pairToolProgressSteps(progress, steps).get("tool:rag__rag_search#2")?.id).toBe("s2");
  });

  // #2373
  it("段の終わりのイベントの詳細は、結果をまだ持たない step で上書きしない", () => {
    const eventDetails = { fields: [{ label: "ツール", value: "rag__rag_search", mono: true }], lines: ["イベントの詳細"] };
    const progress = [
      { id: "tool:rag__rag_search", label: "ツール rag__rag_search を呼びました", status: "done" as const, details: eventDetails },
    ];
    const [kept] = withToolStepDetails(progress, [toolStep("s1", "rag__rag_search", { status: "running" })]);
    expect(kept.details).toEqual(eventDetails);
    const [replaced] = withToolStepDetails(progress, [
      toolStep("s1", "rag__rag_search", {
        tool_result: { name: "rag__rag_search", success: true, output: { answer: "回答 A" } } as never,
      }),
    ]);
    expect(replaced.details?.code?.[1].text).toContain("回答 A");
  });

  it("長い結果は先頭だけにして省略の印を付け、ツールの呼び出しでなければ詳細を付けない", () => {
    const long = toolStep("s1", "nl2sql__query", {
      tool_call: { name: "nl2sql__query", arguments: {} },
      tool_result: { name: "nl2sql__query", success: true, output: { text: "x".repeat(10_000) } } as never,
    });
    const details = toolStepDetails(long);
    expect(details?.truncated).toBe(true);
    expect(details?.code).toHaveLength(1);
    // 引数も結果も無いツールの段も、内部名だけは詳細に出す。
    expect(toolStepDetails(toolStep("s2", "a", { tool_call: { name: "a", arguments: {} } }))).toEqual({
      fields: [{ label: "ツール", value: "a", mono: true }],
    });
    expect(toolStepDetails(toolStep("s3", "a", { tool_call: undefined }))).toBeUndefined();
  });
});
