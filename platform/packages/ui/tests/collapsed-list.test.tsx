// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { COLLAPSED_LIST_LIMIT, CollapsedListToggle, RunTimeline, useCollapsedList, type RunTimelineStep } from "../src";

// #4636: 会話・実行の詳細の中で増えるブロックは、入れ子のスクロールにせず上限を超えたら畳む。

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

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
});

function Items({ count, keep }: { count: number; keep?: "start" | "end" }) {
  const items = Array.from({ length: count }, (_, index) => `項目 ${index + 1}`);
  const state = useCollapsedList(items, { keep, pinned: (item) => item === "項目 2" });
  return (
    <div>
      {keep === "end" ? <CollapsedListToggle state={state} testId="toggle" /> : null}
      <ul>
        {state.visible.map(({ item }) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      {keep !== "end" ? <CollapsedListToggle state={state} testId="toggle" /> : null}
    </div>
  );
}

function texts() {
  return Array.from(host.querySelectorAll("li")).map((item) => item.textContent);
}

function toggle() {
  return host.querySelector<HTMLButtonElement>('[data-testid="toggle"]');
}

describe("useCollapsedList / CollapsedListToggle", () => {
  it("上限以下は畳まずボタンも出さない", () => {
    act(() => root.render(<Items count={COLLAPSED_LIST_LIMIT} />));
    expect(texts()).toHaveLength(COLLAPSED_LIST_LIMIT);
    expect(toggle()).toBeNull();
  });

  it("先頭を残して畳み、「すべて表示（全 N 件）」で開いて「折りたたむ」で戻す", () => {
    act(() => root.render(<Items count={25} />));
    expect(texts()).toHaveLength(COLLAPSED_LIST_LIMIT);
    expect(texts()[0]).toBe("項目 1");
    expect(toggle()?.textContent).toBe("すべて表示（全 25 件）");
    expect(toggle()?.getAttribute("aria-expanded")).toBe("false");
    act(() => toggle()?.click());
    expect(texts()).toHaveLength(25);
    expect(toggle()?.textContent).toBe("折りたたむ");
    act(() => toggle()?.click());
    expect(texts()).toHaveLength(COLLAPSED_LIST_LIMIT);
  });

  it("末尾を残すときは前の項目を畳み、固定する項目は畳んでも出す", () => {
    act(() => root.render(<Items count={25} keep="end" />));
    // 末尾の 10 件 + 固定の「項目 2」。
    expect(texts()).toEqual(["項目 2", ...Array.from({ length: 10 }, (_, index) => `項目 ${index + 16}`)]);
    expect(toggle()?.textContent).toBe("前の 14 件を表示");
  });
});

function steps(count: number, running = false): RunTimelineStep[] {
  return Array.from({ length: count }, (_, index) => ({
    id: `s${index + 1}`,
    label: `段 ${index + 1}`,
    status: running && index === count - 1 ? "running" : index === 3 ? "failed" : "done",
  }));
}

describe("RunTimeline の畳み方", () => {
  it("終わった一覧は先頭を残し、失敗の段は畳んでも出す", () => {
    act(() => root.render(<RunTimeline steps={steps(30)} showBars={false} testId="timeline" />));
    const rows = host.querySelectorAll("[data-run-timeline-step]");
    expect(rows).toHaveLength(COLLAPSED_LIST_LIMIT);
    expect(rows[0].getAttribute("data-run-timeline-step")).toBe("s1");
    const button = host.querySelector<HTMLButtonElement>('[data-testid="timeline-collapse-toggle"]');
    expect(button?.textContent).toBe("すべて表示（全 30 件）");
    act(() => button?.click());
    expect(host.querySelectorAll("[data-run-timeline-step]")).toHaveLength(30);
  });

  it("実行中は最新の段を残して、前の段を開くボタンを一覧の前に置く", () => {
    act(() => root.render(<RunTimeline steps={steps(30, true)} showBars={false} testId="timeline" />));
    const ids = Array.from(host.querySelectorAll("[data-run-timeline-step]")).map((row) => row.getAttribute("data-run-timeline-step"));
    // 失敗の段 s4 + 末尾の 10 段。
    expect(ids).toEqual(["s4", ...Array.from({ length: 10 }, (_, index) => `s${index + 21}`)]);
    const button = host.querySelector<HTMLButtonElement>('[data-testid="timeline-collapse-toggle"]');
    expect(button?.textContent).toBe("前の 19 件を表示");
    const list = host.querySelector("ol");
    expect(button && list ? button.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING : 0).toBeTruthy();
  });
});
