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
    const body = host.querySelector<HTMLElement>('[data-testid="evidence-reader-content"]')!;
    const move = vi.spyOn(body, "scrollIntoView");
    body.scrollTop = 60;
    const button = (text: string) => Array.from(host.querySelectorAll("button")).find(node => node.textContent?.includes(text))!;
    await act(async () => { button("後文を読む").click(); await settle(); });
    expect(host.textContent).toContain("合成の引用"); expect(host.textContent).toContain("取得失敗");
    expect(body.scrollTop).toBe(60); expect(move).not.toHaveBeenCalled();
    await act(async () => { button("再試行").click(); await settle(); });
    expect(host.textContent).toContain("合成の引用"); expect(host.textContent).toContain("追加の本文");
    expect(host.textContent).toContain("この引用には含まれません");
    expect(move).toHaveBeenCalledWith({block:"nearest",inline:"nearest"});
    expect(document.activeElement).toBe(body);
    expect(host.querySelector('[data-reader-current="true"]')?.textContent).toContain("追加の本文");
    expect(host.querySelector('[data-testid="evidence-reader-location"]')?.textContent).toBe("後文へ移動しました。");
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


it("前文へ移動すると取得した窓を現在地にし、章節の位置も更新する", async () => {
  const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
  const load = vi.fn().mockResolvedValueOnce({...windowData, previous_cursor:"previous"})
    .mockResolvedValueOnce({...windowData, section_path:["前の章"], blocks:[{id:"previous",offset:0,text:"合成の前文",highlights:[],cited:false}]});
  await act(async () => { root.render(<EvidenceReader readingKey="a" loadWindow={load} />); await settle(); });
  const body = host.querySelector<HTMLElement>('[data-testid="evidence-reader-content"]')!;
  const move = vi.spyOn(body, "scrollIntoView");
  const before = Array.from(host.querySelectorAll("button")).find(node => node.textContent?.includes("前文を読む"))!;
  await act(async () => { before.click(); await settle(); });
  expect(move).toHaveBeenCalledTimes(1);
  expect(document.activeElement).toBe(body);
  expect(host.querySelector('[data-reader-current="true"]')?.textContent).toContain("合成の前文");
  expect(host.textContent).toContain("前の章");
  expect(host.querySelector('[data-testid="evidence-reader-location"]')?.textContent).toBe("前文へ移動しました。");
  await act(async () => root.unmount()); host.remove();
});


it("同じ引用の受動的な再取得では、読みかけのscrollとfocusを移さない", async () => {
  const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
  const first = vi.fn().mockResolvedValue(windowData);
  await act(async () => { root.render(<EvidenceReader readingKey="a" loadWindow={first} />); await settle(); });
  const body = host.querySelector<HTMLElement>('[data-testid="evidence-reader-content"]')!;
  body.scrollTop = 60;
  const move = vi.spyOn(body, "scrollIntoView");
  const refresh = vi.fn().mockResolvedValue({...windowData});
  await act(async () => { root.render(<EvidenceReader readingKey="a" loadWindow={refresh} />); await settle(); });
  expect(refresh).toHaveBeenCalledTimes(1);
  expect(body.scrollTop).toBe(60);
  expect(move).not.toHaveBeenCalled();
  expect(document.activeElement).not.toBe(body);
  await act(async () => root.unmount()); host.remove();
});


it("引用位置への戻りは先頭の参考文脈ではなく引用した段落を現在地にする", async () => {
  const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
  const load = vi.fn().mockResolvedValue({...windowData, blocks:[
    {id:"context",offset:0,text:"合成の前文",highlights:[],cited:false},
    ...windowData.blocks,
  ]});
  await act(async () => { root.render(<EvidenceReader readingKey="a" loadWindow={load} />); await settle(); });
  const anchor = Array.from(host.querySelectorAll("button")).find(node => node.textContent?.includes("引用位置に戻る"))!;
  await act(async () => { anchor.click(); await settle(); });
  expect(host.querySelector('[data-reader-current="true"]')?.textContent).toContain("合成の引用");
  expect(host.querySelector('[data-testid="evidence-reader-location"]')?.textContent).toBe("引用位置へ戻りました。");
  await act(async () => root.unmount()); host.remove();
});
