// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  DEFAULT_RUN_STATUS_LABELS,
  RUN_STATUSES,
  RunDetailLayout,
  RunDetailSection,
  RunHistoryTable,
  RunHistoryToolbar,
  RunStatusBadge,
  RunSummary,
  RunTimeline,
  formatRunDateTime,
  formatRunDuration,
  formatRunTokens,
  isRunActive,
  runPeriodSince,
  runStatusIcon,
  runStatusVariant,
  runTimelineBars,
  type RunHistoryItem,
  type RunTimelineStep,
} from "../src";

// #1955: 実行履歴の統一の型（3 製品の一覧・詳細の共通の部品）。

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function render(node: React.ReactNode) {
  act(() => root.render(node));
}

describe("実行の状態", () => {
  it("7 つの状態はアイコンがすべて違い、色だけで区別しない", () => {
    const icons = new Set(RUN_STATUSES.map((status) => runStatusIcon(status)));
    expect(icons.size).toBe(RUN_STATUSES.length);
  });

  it("状態の色（variant）は意味の系統に写す", () => {
    expect(runStatusVariant("succeeded")).toBe("success");
    expect(runStatusVariant("failed")).toBe("danger");
    expect(runStatusVariant("running")).toBe("info");
    expect(runStatusVariant("waiting")).toBe("warning");
    expect(runStatusVariant("blocked")).toBe("warning");
    expect(runStatusVariant("cancelled")).toBe("neutral");
    expect(runStatusVariant("queued")).toBe("neutral");
  });

  it("終わっていない状態は待機中・実行中・応答待ちだけ", () => {
    expect(RUN_STATUSES.filter(isRunActive)).toEqual(["queued", "running", "waiting"]);
  });

  it("バッジは文言・アイコン・data-run-status を持ち、製品の文言で上書きできる", () => {
    const html = renderToStaticMarkup(<RunStatusBadge status="waiting" label="承認待ち" />);
    expect(html).toContain('data-run-status="waiting"');
    expect(html).toContain("承認待ち");
    expect(html).toContain("<svg");
    expect(renderToStaticMarkup(<RunStatusBadge status="failed" />)).toContain(DEFAULT_RUN_STATUS_LABELS.failed);
    expect(renderToStaticMarkup(<RunStatusBadge status="failed" labels={{ failed: "エラー" }} />)).toContain("エラー");
  });
});

describe("書式と絞り込みの値", () => {
  it("所要時間はチャットの経過と同じ書式、記録なしは「—」", () => {
    expect(formatRunDuration(1234)).toBe("1.2 秒");
    expect(formatRunDuration(125_000)).toBe("2 分 5 秒");
    expect(formatRunDuration(null)).toBe("—");
    expect(formatRunDuration(undefined, "記録なし")).toBe("記録なし");
  });

  it("日時は端末のタイムゾーンの絶対時刻（相対時刻にしない）", () => {
    const iso = "2026-10-15T00:12:05.000Z";
    const date = new Date(iso);
    const pad = (value: number) => String(value).padStart(2, "0");
    const expected = `${date.getFullYear()}/${pad(date.getMonth() + 1)}/${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
    expect(formatRunDateTime(iso)).toBe(expected);
    expect(formatRunDateTime(iso, { seconds: true })).toBe(`${expected}:${pad(date.getSeconds())}`);
    expect(formatRunDateTime(null)).toBe("—");
    expect(formatRunDateTime("not-a-date")).toBe("not-a-date");
  });

  it("token は桁区切り", () => {
    expect(formatRunTokens(12345)).toBe("12,345 token");
    expect(formatRunTokens(null)).toBe("—");
  });

  it("期間の開始は now から引いた ISO、すべては null", () => {
    const now = new Date("2026-10-15T12:00:00.000Z");
    expect(runPeriodSince("all", now)).toBeNull();
    expect(runPeriodSince("24h", now)).toBe("2026-10-14T12:00:00.000Z");
    expect(runPeriodSince("7d", now)).toBe("2026-10-08T12:00:00.000Z");
    expect(runPeriodSince("30d", now)).toBe("2026-09-15T12:00:00.000Z");
  });
});

interface ProductRow {
  run_id: string;
  question: string;
  status: "ok" | "ng";
  by: string;
}

const ROWS: ProductRow[] = [
  { run_id: "r1", question: "先月の売上は？", status: "ok", by: "me" },
  { run_id: "r2", question: "在庫の少ない商品を教えて", status: "ng", by: "佐藤" },
];

function toItem(row: ProductRow): RunHistoryItem {
  return {
    id: row.run_id,
    title: row.question,
    target: "販売分析",
    href: `/history?id=${row.run_id}`,
    status: row.status === "ok" ? "succeeded" : "failed",
    kind: "チャット",
    executor: row.by === "me" ? { name: "山田", isSelf: true } : { name: row.by },
    startedAt: "2026-10-15T00:12:05.000Z",
    durationMs: row.status === "ok" ? 3200 : null,
  };
}

describe("RunHistoryTable", () => {
  it("列の順は 質問 → 状態 → 種類 → 実行者 → 開始日時 → 所要時間 → 製品の列 → 操作", () => {
    render(
      <RunHistoryTable
        rows={ROWS}
        toItem={toItem}
        onOpen={() => undefined}
        showExecutor
        extraColumns={[{ key: "rows", header: "行数", render: () => "10" }]}
        actionsFor={() => [{ id: "rerun", label: "再実行", onSelect: () => undefined }]}
        testId="runs"
      />
    );
    const headers = Array.from(container.querySelectorAll("thead th")).map((th) => th.textContent?.trim());
    expect(headers).toEqual(["質問", "状態", "種類", "実行者", "開始日時", "所要時間", "行数", "操作"]);
    // 広い置き場所だけの列（狭いと題名の下の 1 行）。製品の列も同じ扱い。
    const statusHeader = container.querySelectorAll("thead th")[1];
    expect(statusHeader.className).toContain("hidden");
    expect(statusHeader.className).toContain("@4xl:table-cell");
    expect(container.querySelectorAll("thead th")[6].className).toContain("@4xl:table-cell");
    expect(container.querySelector("[data-run-history-table]")?.className).toContain("@container");
  });

  it("題名は詳細へのリンク（href・aria-current）で、狭い画面の 1 行に状態・日時・所要時間・実行者を持つ", () => {
    render(
      <RunHistoryTable rows={ROWS} toItem={toItem} onOpen={() => undefined} showExecutor selectedRunId="r2" />
    );
    const links = container.querySelectorAll<HTMLAnchorElement>("a[data-run-id]");
    expect(links).toHaveLength(2);
    expect(links[0].getAttribute("href")).toBe("/history?id=r1");
    expect(links[1].getAttribute("aria-current")).toBe("true");
    const meta = container.querySelectorAll("[data-run-history-compact-meta]");
    expect(meta[0].className).toContain("@4xl:hidden");
    expect(meta[0].textContent).toContain("完了");
    expect(meta[0].textContent).toContain("3.2 秒");
    expect(meta[0].textContent).toContain("自分");
    expect(meta[1].textContent).toContain("失敗");
    expect(meta[1].textContent).toContain("—");
    expect(meta[1].textContent).toContain("佐藤");
    expect(meta[0].querySelector("time")?.getAttribute("dateTime")).toBe("2026-10-15T00:12:05.000Z");
  });

  it("実行者が自分の行は「自分」、列は渡したときだけ出す", () => {
    render(<RunHistoryTable rows={ROWS} toItem={toItem} onOpen={() => undefined} />);
    const headers = Array.from(container.querySelectorAll("thead th")).map((th) => th.textContent?.trim());
    expect(headers).not.toContain("実行者");
    expect(headers).not.toContain("操作");
    expect(headers).not.toContain("token / 費用");
  });

  it("題名のクリック（修飾キーなし）と行のクリックで onOpen を呼ぶ", () => {
    const onOpen = vi.fn();
    render(<RunHistoryTable rows={ROWS} toItem={toItem} onOpen={onOpen} />);
    act(() => {
      container.querySelector<HTMLAnchorElement>("a[data-run-id='r1']")?.click();
    });
    expect(onOpen).toHaveBeenLastCalledWith(ROWS[0]);
    act(() => {
      container.querySelectorAll<HTMLTableRowElement>("tbody tr")[1].querySelector("td")?.click();
    });
    expect(onOpen).toHaveBeenLastCalledWith(ROWS[1]);
  });

  it("利用量の列は token と費用、記録が無ければ「—」", () => {
    render(
      <RunHistoryTable
        rows={ROWS}
        toItem={(row) => ({ ...toItem(row), usage: row.status === "ok" ? { tokens: 1500, cost: "$0.01" } : null })}
        onOpen={() => undefined}
        showUsage
      />
    );
    const cells = Array.from(container.querySelectorAll("tbody tr")).map((tr) => tr.lastElementChild?.textContent);
    expect(cells[0]).toBe("1,500 token$0.01");
    expect(cells[1]).toBe("—");
  });

  it("文言は部分で上書きできる（Agent の「依頼」など）", () => {
    render(
      <RunHistoryTable rows={ROWS} toItem={toItem} onOpen={() => undefined} labels={{ columns: { title: "依頼" } }} />
    );
    expect(container.querySelector("thead th")?.textContent).toBe("依頼");
    expect(container.querySelectorAll("thead th")[1].textContent).toBe("状態");
  });
});

describe("RunHistoryToolbar", () => {
  it("絞り込みを 状態 → 種類 → 期間 → 実行者 の順に FilterSelect で並べ、既定と違う値の数で「条件をクリア」を出す", () => {
    const onClear = vi.fn();
    render(
      <RunHistoryToolbar
        status={{ value: "failed", options: ["succeeded", "failed"], onChange: () => {} }}
        kind={{ value: "", options: [{ value: "", label: "すべて" }, { value: "chat", label: "チャット" }], onChange: () => {} }}
        period={{ value: "all", onChange: () => {} }}
        executor={{ value: "all", onChange: () => {} }}
        onClearFilters={onClear}
        testId="runs"
      />
    );
    const selects = [...container.querySelectorAll<HTMLElement>("[data-filter-active]")];
    expect(selects.map((el) => el.dataset.filterActive)).toEqual(["true", "false", "false", "false"]);
    expect(container.textContent).toContain("状態");
    expect(container.textContent).toContain("失敗");
    expect(container.querySelector("[data-list-toolbar]")?.getAttribute("data-list-toolbar-filters-layout")).toBeTruthy();
    const clear = container.querySelector<HTMLButtonElement>("[data-list-toolbar-clear]");
    expect(clear).not.toBeNull();
    act(() => clear!.click());
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it("絞り込みが既定のままなら「条件をクリア」を出さない", () => {
    render(
      <RunHistoryToolbar
        status={{ value: "all", options: ["succeeded"], onChange: () => {} }}
        period={{ value: "all", onChange: () => {} }}
        onClearFilters={() => {}}
      />
    );
    expect(container.querySelector("[data-list-toolbar-clear]")).toBeNull();
    expect(container.querySelectorAll("[data-filter-active]")).toHaveLength(2);
  });

  it("種類の既定は defaultValue（品質評価以外）を基準にし、「すべて」を選ぶと絞り込みとして数える（#1983）", () => {
    const options = [
      { value: "not_evaluation", label: "品質評価以外" },
      { value: "all", label: "すべて" },
      { value: "evaluation", label: "品質評価" },
    ];
    render(
      <RunHistoryToolbar
        kind={{ value: "not_evaluation", defaultValue: "not_evaluation", options, onChange: () => {} }}
        onClearFilters={() => {}}
      />
    );
    expect(container.querySelector("[data-list-toolbar-clear]")).toBeNull();
    expect(container.querySelector<HTMLElement>("[data-filter-active]")?.dataset.filterActive).toBe("false");
    expect(container.textContent).toContain("品質評価以外");

    render(
      <RunHistoryToolbar
        kind={{ value: "all", defaultValue: "not_evaluation", options, onChange: () => {} }}
        onClearFilters={() => {}}
      />
    );
    expect(container.querySelector("[data-list-toolbar-clear]")).not.toBeNull();
    expect(container.querySelector<HTMLElement>("[data-filter-active]")?.dataset.filterActive).toBe("true");
  });
});

describe("RunSummary", () => {
  it("事実は共通の順に並べ、記録の無い事実は出さない。実行 ID は最後", () => {
    render(
      <RunSummary
        status="succeeded"
        question={"先月の売上は？\n地域別に"}
        startedAt="2026-10-15T00:12:05.000Z"
        durationMs={3200}
        kind="チャット"
        executor={{ name: "管理者" }}
        owner={{ name: "山田", isSelf: true }}
        target={{ label: "業務プロファイル", value: "販売分析" }}
        model="openai.gpt-oss-120b"
        usage={{ tokens: 2000 }}
        extraFacts={[{ id: "rows", label: "行数", value: "12" }]}
        runId="run-123"
        testId="summary"
      />
    );
    const facts = Array.from(container.querySelectorAll("[data-run-fact]")).map((node) => node.getAttribute("data-run-fact"));
    expect(facts).toEqual(["startedAt", "duration", "kind", "executor", "owner", "target", "model", "tokens", "rows", "runId"]);
    expect(container.querySelector("[data-run-fact='owner'] dd")?.textContent).toBe("自分");
    expect(container.querySelector("[data-run-fact='target'] dt")?.textContent).toBe("業務プロファイル");
    expect(container.querySelector("[data-run-question]")?.textContent).toContain("地域別に");
    expect(container.querySelector("[data-run-status]")?.getAttribute("data-run-status")).toBe("succeeded");
  });
});

describe("RunTimeline", () => {
  it("時刻のある段は実際の位置、無い段は前の段の後ろに積む", () => {
    const steps: RunTimelineStep[] = [
      { id: "a", label: "A", status: "done", startedAt: "2026-10-15T00:00:00.000Z", finishedAt: "2026-10-15T00:00:01.000Z" },
      { id: "b", label: "B", status: "done", startedAt: "2026-10-15T00:00:01.000Z", finishedAt: "2026-10-15T00:00:04.000Z" },
    ];
    expect(runTimelineBars(steps)).toEqual([
      { offset: 0, width: 25 },
      { offset: 25, width: 75 },
    ]);
    const sequential: RunTimelineStep[] = [
      { id: "a", label: "A", status: "done", durationMs: 1000 },
      { id: "b", label: "B", status: "done", durationMs: 0 },
      { id: "c", label: "C", status: "done", durationMs: 3000 },
      { id: "d", label: "D", status: "pending" },
    ];
    expect(runTimelineBars(sequential)).toEqual([
      { offset: 0, width: 25 },
      { offset: 25, width: 0.5 },
      { offset: 25, width: 75 },
      null,
    ]);
    expect(runTimelineBars([{ id: "x", label: "X", status: "pending" }])).toEqual([null]);
  });

  it("段は名前・所要時間を文字で出し、失敗した段は最初から開き、開閉は aria-expanded で伝える", () => {
    render(
      <RunTimeline
        steps={[
          { id: "plan", label: "計画", status: "done", durationMs: 1200 },
          { id: "tool", label: "ツール: search", status: "done", durationMs: 800, level: 1, children: <p>引数と結果</p> },
          { id: "sql", label: "SQL を実行", status: "failed", durationMs: 300, children: <p>ORA-00942</p> },
        ]}
        testId="timeline"
      />
    );
    expect(container.textContent).toContain("2 ステップ・合計 1.5 秒");
    const buttons = container.querySelectorAll<HTMLButtonElement>("button[aria-expanded]");
    expect(buttons).toHaveLength(2);
    expect(buttons[0].getAttribute("aria-expanded")).toBe("false");
    expect(buttons[1].getAttribute("aria-expanded")).toBe("true");
    expect(container.textContent).toContain("ORA-00942");
    expect(container.textContent).not.toContain("引数と結果");
    // 失敗は文字でも出す（色だけにしない）。完了は読み上げの文言。
    expect(container.querySelector("[data-run-timeline-step='sql']")?.textContent).toContain("失敗 · 0.3 秒");
    expect(container.querySelector("[data-run-timeline-step='plan'] .sr-only")?.textContent).toContain("完了");
    // 入れ子の段は字下げする。時間の線は飾り（aria-hidden）。
    expect(container.querySelector("[data-run-timeline-step='tool'] .pl-6")).not.toBeNull();
    expect(container.querySelector("[data-run-timeline-step='plan'] .pl-6")).toBeNull();
    expect(container.querySelector("[data-run-timeline-bar]")?.getAttribute("aria-hidden")).toBe("true");
    act(() => buttons[0].click());
    expect(buttons[0].getAttribute("aria-expanded")).toBe("true");
    expect(container.textContent).toContain("引数と結果");
    const region = document.getElementById(buttons[0].getAttribute("aria-controls") ?? "");
    expect(region?.hidden).toBe(false);
  });
});

describe("RunDetailLayout / RunDetailSection", () => {
  it("節の順を固定する（概要 → 回答 → 処理の経過 → 根拠 → フィードバック → 技術的な詳細）", () => {
    render(
      <RunDetailLayout
        technical={<p>T</p>}
        feedback={<p>F</p>}
        evidence={<p>E</p>}
        timeline={<p>P</p>}
        answer={<p>A</p>}
        summary={<p>S</p>}
      />
    );
    expect(container.querySelector("[data-run-detail]")?.textContent).toBe("SAPEFT");
  });

  it("開閉できる節は既定で閉じた Disclosure、通常の節はカード", () => {
    render(
      <>
        <RunDetailSection title="技術的な詳細" collapsible testId="tech">
          <p>raw</p>
        </RunDetailSection>
        <RunDetailSection title="回答" testId="answer">
          <p>本文</p>
        </RunDetailSection>
      </>
    );
    const tech = container.querySelector<HTMLDetailsElement>("[data-testid='tech']");
    expect(tech?.tagName).toBe("DETAILS");
    expect(tech?.open).toBe(false);
    expect(container.querySelector("[data-testid='answer'] h2")?.textContent).toBe("回答");
  });

  it("開閉できる節は open / onOpenChange で受控にできる（開いたときだけ中身を読む節）", () => {
    const onOpenChange = vi.fn();
    render(
      <RunDetailSection title="監査ログ" collapsible open={false} onOpenChange={onOpenChange} testId="audit">
        <p>audit</p>
      </RunDetailSection>
    );
    const audit = container.querySelector<HTMLDetailsElement>("[data-testid='audit']");
    expect(audit?.open).toBe(false);
    act(() => audit?.querySelector("summary")?.click());
    expect(onOpenChange).toHaveBeenCalledWith(true);
  });
});
