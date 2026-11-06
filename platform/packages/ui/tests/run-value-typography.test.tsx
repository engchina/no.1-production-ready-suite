// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it } from "vitest";
import { DEFAULT_RUN_STEP_DETAILS_LABELS as labels, RunStepDetailsView, RunStepTiming, runStepTechnicalFields } from "../src/components/data/run-step-details";
import { RunSummary, RunUsedModels } from "../src/components/data/run-detail";
import { TraceExplorer } from "../src/components/data/trace-explorer";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const timing = { startedAt: "2026-01-01T00:00:00.123456789Z", finishedAt: "2026-01-01T00:00:00.123456789Z", durationMs: 0 };
const call = { input: {status: "recorded" as const, text: JSON.stringify({model: "provider.synthetic", temperature: 0, reasoning: {effort: "low"}})}, output: {status: "recorded" as const, text: JSON.stringify({usage: {input_tokens: 0, output_tokens: 12}})}, schema: {status: "not_recorded" as const, text: ""} };
function withHost(check: (host: HTMLDivElement, render: (node: React.ReactNode) => void) => void) {
  const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
  try { check(host, node => act(() => root.render(node))); }
  finally { act(() => root.unmount()); host.remove(); }
}
const fields = (host: HTMLDivElement) => Array.from(host.querySelectorAll("dt")).map(label => ({label, value: label.nextElementSibling as HTMLElement}));
describe("実行値の表示意味と書体", () => {
  it("実時刻だけ等幅にし、ラベルと欠測代替文は本文書体に保つ", () => withHost((host, render) => {
    render(<RunStepTiming startedAt={timing.startedAt} />);
    const rows = fields(host);
    expect(rows[0].value.classList.contains("font-mono")).toBe(true);
    expect(rows[0].label.classList.contains("font-mono")).toBe(false);
    expect(rows[1].value.classList.contains("font-mono")).toBe(false);
    expect(rows[0].value.querySelector("time")?.getAttribute("datetime")).toBe(timing.startedAt);
  }));
  it("時間タブも実時刻は等幅、実測0の所要時間は本文+等幅数字で表示する", () => withHost((host, render) => {
    render(<RunStepDetailsView details={{}} forceTabs activeTab="timing" timing={timing} />);
    const rows = fields(host);
    expect(rows.slice(0, 2).every(row => row.value.classList.contains("font-mono"))).toBe(true);
    expect(rows[2].value.classList.contains("tnum")).toBe(true);
    expect(rows[2].value.classList.contains("font-mono")).toBe(false);
    render(<RunStepDetailsView details={{}} forceTabs activeTab="timing" timing={{}} />);
    expect(fields(host).every(row => !row.value.classList.contains("font-mono") && !row.value.classList.contains("tnum"))).toBe(true);
  }));
  it("呼出統計の実ID・数値・通常文字を概要と技術欄で同じ意味にする", () => withHost((host, render) => {
    const technical = runStepTechnicalFields({call});
    for (const details of [{call}, {fields: technical}]) {
      render(<RunStepDetailsView details={details} />);
      const rows = fields(host);
      const value = (label: string) => rows.find(row => row.label.textContent === label)!.value;
      expect(value(labels.model).classList.contains("font-mono")).toBe(true);
      expect(value(labels.inputTokens).classList.contains("tnum")).toBe(true);
      expect(value(labels.inputTokens).classList.contains("font-mono")).toBe(false);
      expect(value(labels.effort).classList.contains("font-mono")).toBe(false);
    }
  }));
  it("モデル実IDの明示と記録イベントだけ等幅にし、表示名と意味未指定は通常書体にする", () => withHost((host, render) => {
    render(<RunUsedModels models={[
      {label: "LLM", value: "表示モデル", modelId: "provider.named"},
      {label: "LLM", value: "provider.explicit", valueKind: "identifier"},
      {label: "LLM", value: "意味未指定"},
    ]} events={[{type: "step", attributes: {"gen_ai.request.model": "provider.recorded"}}]} />);
    const values = Array.from(host.querySelectorAll("dd"));
    expect(values.map(value => value.classList.contains("font-mono"))).toEqual([false, true, false, true]);
  }));
});

it("概要の比較値は実時刻monoと有限数値のtnumを区別する", () => withHost((host, render) => {
  render(<RunSummary status="succeeded" question="合成の質問" startedAt={timing.startedAt} finishedAt={timing.finishedAt} durationMs={0} usage={{tokens: 0, cost: "$0.00"}} runId="synthetic-run" extraFacts={[{id: "rows", label: "行数", value: "0", tabular: true}]} />);
  for (const id of ["startedAt", "finishedAt"]) expect(host.querySelector(`[data-run-fact="${id}"] dd`)?.classList.contains("font-mono")).toBe(true);
  for (const id of ["duration", "tokens", "rows", "cost"]) {
    const value = host.querySelector(`[data-run-fact="${id}"] dd`)!;
    expect(value.classList.contains("tnum")).toBe(true);
    expect(value.classList.contains("font-mono")).toBe(false);
  }
}));
it("概要の比較対象でない種類・実行者・未記録は通常表示に保つ", () => withHost((host, render) => {
  render(<RunSummary status="succeeded" question="合成の質問" startedAt="" durationMs={null} usage={{tokens: null, note: "未計測"}} kind="チャット" executor={{name: "合成利用者"}} runId="synthetic-run" />);
  expect(host.querySelector('[data-run-fact="cost"]')).toBeNull();
  for (const id of ["startedAt", "duration", "tokens", "kind", "executor"]) {
    const value = host.querySelector(`[data-run-fact="${id}"] dd`)!;
    expect(value.classList.contains("tnum")).toBe(false);
    expect(value.classList.contains("font-mono")).toBe(false);
  }
}));

it("軌跡の選択詳細でも実測0と実行中の終了未記録を混同しない", () => withHost((host, render) => {
  render(<TraceExplorer steps={[{id: "zero", label: "合成の実測0", status: "done", startedAt: timing.startedAt, finishedAt: timing.finishedAt}]} onClose={() => undefined} />);
  expect(host.querySelector("[data-trace-duration]")?.classList.contains("tnum")).toBe(true);
  render(<TraceExplorer steps={[{id: "running", label: "合成の実行中", status: "running", startedAt: timing.startedAt}]} onClose={() => undefined} />);
  expect(host.querySelector("[data-trace-duration]")?.classList.contains("tnum")).toBe(false);
  const rows = Array.from(host.querySelectorAll("[data-trace-timing] dd"));
  expect(rows.map(row => row.classList.contains("font-mono"))).toEqual([true, false]);
}));
