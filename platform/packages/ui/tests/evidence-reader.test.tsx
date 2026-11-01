// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";
import { EvidenceReader, type EvidenceReadingWindow } from "../src";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const windowData: EvidenceReadingWindow = {
  revision: "v1", anchor_status: "exact", section_path: ["章 A"], scope: "paragraph",
  blocks: [{ id: "e1", text: "合成の引用", offset: 0, highlights: [[0, 5]], cited: true }],
  previous_cursor: null, next_cursor: "next", outline: [],
};
const settle = () => new Promise(resolve => setTimeout(resolve, 0));

describe("EvidenceReader", () => {
  it("閉じると読み取りを中断し、閉じている間は取得しない", async () => {
    const host = document.createElement("div"); document.body.append(host);
    const root = createRoot(host); let signal: AbortSignal | undefined;
    const load = vi.fn((_input: unknown, nextSignal: AbortSignal) => { signal = nextSignal; return new Promise<EvidenceReadingWindow>(() => {}); });
    await act(async () => root.render(<EvidenceReader readingKey="d1:c1" loadWindow={load} enabled={false} />));
    expect(load).not.toHaveBeenCalled();
    await act(async () => root.render(<EvidenceReader readingKey="d1:c1" loadWindow={load} />));
    expect(host.querySelector('[data-testid="evidence-reader-loading"]')).not.toBeNull();
    await act(async () => root.unmount());
    expect(signal?.aborted).toBe(true); host.remove();
  });
  it("追加読み取りが失敗しても引用を保持し、再試行で続きだけ追加する", async () => {
    const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
    const load = vi.fn().mockResolvedValueOnce(windowData).mockRejectedValueOnce(new Error("取得失敗"))
      .mockResolvedValueOnce({ ...windowData, next_cursor: null, blocks: [{ id: "e2", text: "追加の本文", offset: 0, highlights: [], cited: false }] });
    await act(async () => { root.render(<EvidenceReader readingKey="d1:c1" loadWindow={load} />); await settle(); });
    expect(host.querySelector("mark")?.textContent).toBe("合成の引用");
    const button = (text: string) => Array.from(host.querySelectorAll("button")).find(node => node.textContent?.includes(text))!;
    await act(async () => { button("後文を読む").click(); await settle(); });
    expect(host.textContent).toContain("合成の引用"); expect(host.textContent).toContain("取得失敗");
    await act(async () => { button("再試行").click(); await settle(); });
    expect(host.textContent).toContain("合成の引用"); expect(host.textContent).toContain("追加の本文");
    expect(host.textContent).toContain("この引用には含まれません");
    await act(async () => root.unmount()); host.remove();
  });
});


it("引用元の表は HTML を文字列として並べず、共通の表で列見出しを保持する", async () => {
  const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
  const load = vi.fn().mockResolvedValue({ ...windowData, blocks: [{ id: "t1", kind: "table", text: "<table><tr><th>項目</th><th>値</th></tr><tr><td>A</td><td>1</td></tr></table>", offset: 0, highlights: [], cited: true }] });
  await act(async () => { root.render(<EvidenceReader readingKey="d1:t1" loadWindow={load} />); await settle(); });
  expect(host.querySelector("th")?.textContent).toContain("項目");
  expect(host.querySelector("td")?.textContent).toContain("A");
  await act(async () => root.unmount()); host.remove();
});


it("引用の変更では古い続きの位置を送らず、進行中の取得を中断する", async () => {
  const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
  const signals: AbortSignal[] = [];
  const load = vi.fn((_input: unknown, signal: AbortSignal) => { signals.push(signal); return Promise.resolve(windowData); });
  const button = (text: string) => Array.from(host.querySelectorAll("button")).find(node => node.textContent?.includes(text))!;
  await act(async () => { root.render(<EvidenceReader readingKey="a" loadWindow={load} />); await settle(); });
  await act(async () => { button("後文を読む").click(); await settle(); });
  const previousSignal = signals.at(-1)!;
  await act(async () => { root.render(<EvidenceReader readingKey="b" loadWindow={load} />); await settle(); });
  expect(previousSignal.aborted).toBe(true);
  expect(load.mock.calls.map(args => args[0])).toEqual([{}, {cursor: "next", direction: "after"}, {}]);
  await act(async () => root.unmount()); host.remove();
});

it("目次は選択後に閉じ、章節の冒頭へ移って本文にフォーカスする", async () => {
  const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
  const data = { ...windowData, outline: [{id:"s1", title:"節 A", parent_id:null, has_children:false}] };
  const load = vi.fn().mockResolvedValue(data);
  const button = (text: string) => Array.from(host.querySelectorAll("button")).find(node => node.textContent?.includes(text))!;
  await act(async () => { root.render(<EvidenceReader readingKey="a" loadWindow={load} />); await settle(); });
  await act(async () => { (host.querySelector("summary") as HTMLElement).click(); });
  expect(host.querySelector("details")?.open).toBe(true);
  await act(async () => { button("節 A").click(); await settle(); });
  expect(host.querySelector("details")?.open).toBe(false);
  expect(load.mock.calls.at(-1)?.[0]).toEqual({scope:"section",section_id:"s1"});
  expect(document.activeElement).toBe(host.querySelector('[data-testid="evidence-reader-content"]'));
  await act(async () => { button("親章節を読む").click(); await settle(); });
  expect(load.mock.calls.at(-1)?.[0]).toEqual({scope:"parent"});
  await act(async () => { button("引用位置に戻る").click(); await settle(); });
  expect(load.mock.calls.at(-1)?.[0]).toEqual({});
  await act(async () => root.unmount()); host.remove();
});
