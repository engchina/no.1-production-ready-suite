// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChatProgress, formatChatProgressDuration, type ChatProgressStep } from "../src";

// #1145: チャットの回答の処理の段階（3 製品共通の契約）。

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const T0 = "2026-10-04T00:00:00.000Z";
const at = (seconds: number) => new Date(Date.parse(T0) + seconds * 1000).toISOString();

const runningSteps: ChatProgressStep[] = [
  { id: "queue", label: "処理を開始しました", status: "done", startedAt: at(0), finishedAt: at(0.4) },
  { id: "schema", label: "対象の表を調べました", status: "done", startedAt: at(0.4), finishedAt: at(2) },
  { id: "generate_sql", label: "SQL を生成しています", status: "running", startedAt: at(2), detail: "Select AI" },
  { id: "safety", label: "安全性を確認します", status: "pending" },
];

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.useRealTimers();
});

function render(node: React.ReactNode) {
  act(() => root.render(node));
}

const q = (testId: string) => host.querySelector(`[data-testid="${testId}"]`) as HTMLElement | null;

describe("formatChatProgressDuration", () => {
  it("10 秒未満は 0.1 秒単位、1 分未満は秒、それ以上は分と秒・時間と分", () => {
    expect(formatChatProgressDuration(0)).toBe("0.0 秒");
    expect(formatChatProgressDuration(420)).toBe("0.4 秒");
    expect(formatChatProgressDuration(9_960)).toBe("9.9 秒");
    expect(formatChatProgressDuration(12_400)).toBe("12 秒");
    expect(formatChatProgressDuration(65_000)).toBe("1 分 5 秒");
    expect(formatChatProgressDuration(3_723_000)).toBe("1 時間 2 分");
    expect(formatChatProgressDuration(-5)).toBe("0.0 秒");
  });
});

describe("ChatProgress", () => {
  it("段階が無いときは何も出さない", () => {
    expect(renderToStaticMarkup(<ChatProgress steps={[]} />)).toBe("");
  });

  it("実行中は今の段階 1 行（スピナー・段階・補足・その段階の経過時間）と、完了した段階の畳んだ見出しを出す", () => {
    vi.useFakeTimers();
    vi.setSystemTime(Date.parse(at(5)));
    render(<ChatProgress steps={runningSteps} testId="p" />);
    const rootEl = q("p")!;
    expect(rootEl.dataset.chatProgressState).toBe("running");
    expect(rootEl.getAttribute("aria-busy")).toBe("true");
    const current = q("p-current")!;
    expect(current.dataset.stepId).toBe("generate_sql");
    expect(current.textContent).toContain("SQL を生成しています");
    expect(current.textContent).toContain("（Select AI）");
    // 経過時間は処理全体（最初の段階の開始 0 秒）から数える（#1176。段階ごとに 0 へ戻さない）。
    expect(q("p-timer")!.getAttribute("aria-label")).toBe("経過時間 00:05");
    expect(q("p-timer")!.getAttribute("aria-live")).toBe("off");
    // 動くスピナーは今の段階の 1 つだけ。
    expect(host.querySelectorAll("svg.animate-spin")).toHaveLength(1);
    // 完了した段階は畳む（既定は閉じる）。
    expect(q("p-completed")!.textContent).toContain("2 ステップ完了");
    expect(host.querySelector("details")!.open).toBe(false);
    // 段階の切り替わりの読み上げは今の段階の名前だけ。
    const status = host.querySelector('[role="status"]')!;
    expect(status.textContent).toBe("SQL を生成しています");
  });

  it("完了した段階を開くと、段階ごとの状態（読み上げの語を含む）と所要時間を出す", () => {
    vi.useFakeTimers();
    vi.setSystemTime(Date.parse(at(5)));
    render(<ChatProgress steps={runningSteps} testId="p" />);
    act(() => {
      q("p-completed")!.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    });
    expect(host.querySelector("details")!.open).toBe(true);
    const schema = q("p-step-schema")!;
    expect(schema.dataset.status).toBe("done");
    expect(schema.textContent).toContain("対象の表を調べました");
    expect(schema.textContent).toContain("完了");
    expect(schema.textContent).toContain("1.6 秒");
    // 実行中の段階は畳んだ一覧に入れない（今の段階の行に出す）。
    expect(q("p-step-generate_sql")).toBeNull();
  });

  it("遅延の案内は今の段階の行に付け、行は最初から高さを予約する（スピナーの行を動かさない）", () => {
    vi.useFakeTimers();
    vi.setSystemTime(Date.parse(at(5)));
    render(<ChatProgress steps={runningSteps} testId="p" />);
    expect(q("p-current")!.dataset.slow).toBe("false");
    expect(q("p-slow")).toBeNull();
    expect(host.querySelector('[data-placeholder="通常より時間がかかっています。"]')).not.toBeNull();
    act(() => {
      vi.setSystemTime(Date.parse(at(13)));
      vi.advanceTimersByTime(1000);
    });
    expect(q("p-current")!.dataset.slow).toBe("true");
    expect(q("p-slow")!.textContent).toBe("通常より時間がかかっています。");
  });

  it("段階が変わっても、処理全体の経過時間は 0 に戻らない（#1176）", () => {
    vi.useFakeTimers();
    vi.setSystemTime(Date.parse(at(5)));
    render(<ChatProgress steps={runningSteps} testId="p" />);
    expect(q("p-timer")!.getAttribute("aria-label")).toBe("経過時間 00:05");
    // 次の段階へ進む（安全性の確認の開始 20 秒・今 21 秒）。
    act(() => {
      vi.setSystemTime(Date.parse(at(20)));
      vi.advanceTimersByTime(1000);
    });
    render(
      <ChatProgress
        steps={[
          ...runningSteps.slice(0, 2),
          { ...runningSteps[2]!, status: "done", label: "SQL を生成しました", finishedAt: at(20) },
          { id: "safety", label: "安全性を確認しています", status: "running", startedAt: at(20) },
        ]}
        testId="p"
      />
    );
    expect(q("p-current")!.dataset.stepId).toBe("safety");
    expect(q("p-timer")!.getAttribute("aria-label")).toBe("経過時間 00:21");
    // 段階ごとの所要時間は、完了した段階の行に出す。
    act(() => {
      q("p-completed")!.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    });
    expect(q("p-step-generate_sql")!.textContent).toContain("18 秒");
  });

  it("開始の時刻を渡すと、その時刻から数える（段階に時刻が無いときも全体で数える）", () => {
    vi.useFakeTimers();
    vi.setSystemTime(Date.parse(at(70)));
    render(
      <ChatProgress
        startedAt={at(4)}
        steps={[{ id: "queue", label: "開始を待っています", status: "running" }]}
        testId="p"
      />
    );
    expect(q("p-timer")!.getAttribute("aria-label")).toBe("経過時間 01:06");
  });

  it("終端でないのに今の段階が無い（すべて完了）ときも、今の行（スピナー・「処理を続けています」・経過時間）を出す", () => {
    vi.useFakeTimers();
    vi.setSystemTime(Date.parse(at(30)));
    render(
      <ChatProgress
        active
        steps={[
          { id: "a", label: "受け付けました", status: "done", startedAt: at(0), finishedAt: at(1) },
          { id: "b", label: "SQL を生成しました", status: "done", startedAt: at(1), finishedAt: at(12) },
        ]}
        testId="p"
      />
    );
    const current = q("p-current")!;
    expect(current.dataset.stepId).toBe("");
    expect(current.textContent).toContain("処理を続けています");
    expect(host.querySelectorAll("svg.animate-spin")).toHaveLength(1);
    expect(q("p-timer")!.getAttribute("aria-label")).toBe("経過時間 00:30");
    expect(q("p-completed")!.textContent).toContain("2 ステップ完了");
    expect(host.querySelector('[role="status"]')!.textContent).toBe("処理を続けています");
  });

  it("遅延の案内は今の段階の経過時間で決める（全体が長くても、始まったばかりの段階には付けない）", () => {
    vi.useFakeTimers();
    vi.setSystemTime(Date.parse(at(31)));
    render(
      <ChatProgress
        steps={[
          { id: "a", label: "SQL を生成しました", status: "done", startedAt: at(0), finishedAt: at(30) },
          { id: "b", label: "安全性を確認しています", status: "running", startedAt: at(30) },
        ]}
        testId="p"
      />
    );
    expect(q("p-timer")!.getAttribute("aria-label")).toBe("経過時間 00:31");
    expect(q("p-current")!.dataset.slow).toBe("false");
    act(() => {
      vi.setSystemTime(Date.parse(at(41)));
      vi.advanceTimersByTime(1000);
    });
    expect(q("p-current")!.dataset.slow).toBe("true");
    expect(q("p-slow")!.textContent).toBe("通常より時間がかかっています。");
  });

  it("実行中の段階が無いときは、次の段階を今の段階として出す", () => {
    render(
      <ChatProgress
        steps={[
          { id: "queue", label: "開始を待っています", status: "pending" },
          { id: "generate_sql", label: "SQL を生成します", status: "pending" },
        ]}
        testId="p"
      />
    );
    expect(q("p")!.dataset.chatProgressState).toBe("running");
    expect(q("p-current")!.dataset.stepId).toBe("queue");
    expect(q("p-completed")).toBeNull();
  });

  it("完了後は「処理の経過（N ステップ・M 秒）」の 1 行に畳み、既定は閉じる", () => {
    render(
      <ChatProgress
        steps={[
          { id: "a", label: "受け付けました", status: "done", startedAt: at(0), finishedAt: at(1) },
          { id: "b", label: "SQL を生成しました", status: "done", startedAt: at(1), finishedAt: at(12.5) },
          { id: "c", label: "実行しませんでした", status: "skipped" },
        ]}
        testId="p"
      />
    );
    expect(q("p")!.dataset.chatProgressState).toBe("done");
    expect(q("p")!.getAttribute("aria-busy")).toBe("false");
    expect(q("p-summary")!.textContent).toBe("処理の経過（3 ステップ・12 秒）");
    expect(host.querySelector("details")!.open).toBe(false);
    expect(host.querySelectorAll("svg.animate-spin")).toHaveLength(0);
    expect(q("p-current")).toBeNull();
    // スキップは文字でも出す（色だけに頼らない）。
    expect(q("p-step-c")!.textContent).toContain("スキップ");
    // 完了後は読み上げの文を空にする（回答は会話の欄の log が知らせる）。
    expect(host.querySelector('[role="status"]')!.textContent).toBe("");
  });

  it("失敗した段階があれば開いた状態で出し、失敗を文字とアイコンで示す", () => {
    render(
      <ChatProgress
        steps={[
          { id: "a", label: "受け付けました", status: "done", startedAt: at(0), finishedAt: at(1) },
          { id: "b", label: "SQL を生成できませんでした", status: "failed", startedAt: at(1), finishedAt: at(4) },
          { id: "c", label: "安全性の確認", status: "pending" },
        ]}
        testId="p"
      />
    );
    expect(q("p")!.dataset.chatProgressState).toBe("failed");
    expect(host.querySelector("details")!.open).toBe(true);
    const failed = q("p-step-b")!;
    expect(failed.dataset.status).toBe("failed");
    expect(failed.textContent).toContain("失敗");
    expect(failed.textContent).toContain("3.0 秒");
    expect(q("p-summary")!.textContent).toBe("処理の経過（2 ステップ・4.0 秒）");
  });

  it("active と elapsedMs と labels で上書きできる", () => {
    render(
      <ChatProgress
        active={false}
        elapsedMs={61_000}
        steps={[{ id: "a", label: "x", status: "running", startedAt: at(0) }]}
        labels={{ summary: (count, duration) => `Steps ${count} / ${duration}` }}
        testId="p"
      />
    );
    expect(q("p")!.dataset.chatProgressState).toBe("done");
    expect(q("p-summary")!.textContent).toBe("Steps 0 / 1 分 1 秒");
  });
});
