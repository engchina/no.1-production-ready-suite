import { describe, expect, it } from "vitest";
import { traceTimeline } from "../src/lib/trace-tree";
import { TRACE_DETAILS_JA, createTraceDetailsLabels } from "../src/lib/trace-labels";
import { runTimelineDuration } from "../src/components/data/run-detail";
const at = (n: number) => new Date(Date.UTC(2026, 0, 1) + n).toISOString();
const span = (id: string, parent?: string) => ({ span_id: id.repeat(16), parent_span_id: parent?.repeat(16), trace_id: "a".repeat(32), operation_name: "synthetic", status: "done", started_at: at(0), finished_at: at(2000), duration_ms: 2000 });
describe("安全な trace の表示", () => {
  it("親より先の子・孤立親・cycle を有限回で描き、欠落を明示する", () => {
    const value = traceTimeline({ completeness: "complete", root_span_id: "1".repeat(16), spans: [span("2", "1"), span("1"), span("3", "4"), span("4", "3"), span("5", "6")] });
    expect(value.steps.map(s => s.id)).toEqual(["1", "2", "5", "3", "4"].map(id => id.repeat(16)));
    expect(value.steps.map(s => s.level)).toEqual([0, 1, 0, 0, 1]);
    expect(value.partial).toBe(true);
  });
  it("任意の属性・重複 ID・不正な型を描かない", () => {
    const value = traceTimeline({ completeness: "complete", spans: [{ ...span("1"), attributes: { prompt: "private-payload", "gen_ai.usage.input_tokens": 0 } }, span("1"), null] });
    expect(value.steps).toHaveLength(1);
    expect(JSON.stringify(value)).not.toContain("private-payload");
    expect(value.steps[0].details?.fields?.some(f => f.value === "0")).toBe(true);
    expect(value.partial).toBe(true);
  });
  it("並列の 2 秒の呼び出しを 4 秒と表示しない", () => {
    const steps = traceTimeline({ completeness: "complete", spans: [span("1"), span("2")] }).steps;
    expect(runTimelineDuration(steps)).toBe(2000);
  });
  it("停止を失敗に置き換えず、壊れた記録を許容する", () => {
    const steps = traceTimeline({ spans: [{ ...span("1"), status: "cancelled" }] }).steps;
    expect(steps[0].status).toBe("skipped");
    expect(steps[0].detail).toBe("停止");
    expect(traceTimeline(null).steps).toEqual([]);
  });
});
describe("span の表示名", () => {
  const stage = (id: string, parent: string | undefined, operation_name: string, attributes: Record<string, unknown> = {}) => ({ ...span(id, parent), operation_name, attributes });
  const summary = { completeness: "complete", root_span_id: "1".repeat(16), spans: [
    stage("1", undefined, "op.answer"),
    stage("2", "1", "op.answer", { "pr.stage": "answer" }),
    stage("3", "2", "op.engine.stage", { "pr.stage": "工程 A" }),
    stage("4", "3", "gen_ai.chat", { "gen_ai.request.model": "model-x" }),
    stage("5", "3", "gen_ai.execute_tool", { "gen_ai.tool.name": "tool_y", "gen_ai.request.model": "model-x" }),
  ] };
  it("既定は root を実行の全体、gen_ai をモデル名・ツール名つきにする", () => {
    const steps = traceTimeline(summary).steps;
    expect(steps.map(s => s.label)).toEqual(["実行の全体", "op.answer", "op.engine.stage", "gen_ai.chat · model-x", "gen_ai.execute_tool · tool_y"]);
  });
  it("製品の名前を優先し、詳細に技術の名前と段を残す", () => {
    const labels = createTraceDetailsLabels(key => TRACE_DETAILS_JA[key], {
      spanLabel: ({ operationName, stage: name, root }) => root ? undefined : operationName === "op.engine.stage" ? `表示 ${name}` : name === "answer" ? "回答の段" : null,
    });
    const steps = traceTimeline(summary, labels).steps;
    expect(steps.map(s => s.label).slice(0, 3)).toEqual(["実行の全体", "回答の段", "表示 工程 A"]);
    const fields = steps[2].details?.fields ?? [];
    expect(fields).toContainEqual({ label: "Span name", value: "op.engine.stage", mono: true });
    expect(fields).toContainEqual({ label: "pr.stage", value: "工程 A", mono: true });
  });
});
