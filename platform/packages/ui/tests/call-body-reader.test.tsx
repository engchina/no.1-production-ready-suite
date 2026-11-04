// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";
import { RunStepDetailsView } from "../src/components/data/run-step-details";
import { parseCallBodyPage, parseCallContent, type CallBodyLoader, type CallBodyPage, type CallBodyReference } from "../src/lib/call-body";
import { parseChatProgressStepDetails } from "../src/components/feedback/chat-progress-events";
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const full = JSON.stringify({messages: [{role: "user", content: "合成の質問。"}], tail: "本文の最後"});
const pieces = [full.slice(0, 25), full.slice(25)];
const reference: CallBodyReference = {body_id: "a".repeat(32), sha256: "b".repeat(64), character_count: full.length, byte_count: new TextEncoder().encode(full).length, part_count: 2, visibility: "owner"};
function page(index: number): CallBodyPage {
  const start = index === 0 ? 0 : pieces[0].length;
  return {reference, status: "complete", failure_code: null, parts: [{index, start, end: start + pieces[index].length, text: pieces[index], sha256: "c".repeat(64)}], next_part: index === 0 ? 1 : null};
}
const content = {status: "recorded" as const, text: "preview text", body_reference: reference, preview_only: true};
const call = {input: content, output: {status: "not_recorded" as const, text: ""}, schema: {status: "not_recorded" as const, text: ""}};
function fixture() {
  const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
  return {host, root, dispose: () => {act(() => root.unmount()); host.remove();}};
}
async function button(host: HTMLElement, text: string) {
  const found = [...host.querySelectorAll<HTMLButtonElement>("button")].find(button => button.textContent?.includes(text));
  expect(found, text).toBeDefined(); await act(async () => {found!.click();});
}
describe("全文参照の契約", () => {
  it("不完全状態と正式参照を保持し、非公開の参照は捨てる", () => {
    const raw = {...content, status: "incomplete", failure_code: "queue_full"};
    expect(parseCallContent(raw)).toEqual(raw);
    expect(parseChatProgressStepDetails({call: {input: raw}})?.call?.input).toEqual(raw);
    expect(parseCallContent({...raw, status: "withheld"})).toEqual({status: "withheld", text: ""});
    expect(parseCallContent({...raw, body_reference: {...reference, body_id: "invalid"}}).body_reference).toBeUndefined();
  });
  it("別snapshot・重複・抜け・逆戻り・偽の末尾を拒否する", () => {
    expect(parseCallBodyPage(page(0), reference, 0)).toEqual(page(0));
    expect(parseCallBodyPage({...page(0), reference: {...reference, sha256: "d".repeat(64)}}, reference, 0)).toBeNull();
    expect(parseCallBodyPage({...page(0), next_part: 0}, reference, 0)).toBeNull();
    expect(parseCallBodyPage({...page(0), next_part: null}, reference, 0)).toBeNull();
    expect(parseCallBodyPage({...page(0), parts: [page(0).parts[0], page(0).parts[0]]}, reference, 0)).toBeNull();
    expect(parseCallBodyPage(page(1), reference, 0)).toBeNull();
  });
});
describe("全文の遅延読取", () => {
  it("明示操作で順に読み、最後まで取得してから全文扱いにする", async () => {
    const {host, root, dispose} = fixture();
    const load = vi.fn<CallBodyLoader>().mockImplementation(async request => page(request.firstPart));
    try {
      act(() => root.render(<RunStepDetailsView details={{call}} activeTab="input" spanId="span-a" loadCallBody={load} />));
      expect(load).not.toHaveBeenCalled(); await button(host, "保存済みの全文を読む");
      expect(host.textContent).toContain("続きを読む"); expect(host.textContent).not.toContain("本文の最後");
      await button(host, "続きを読む");
      expect(host.textContent).toContain("本文の最後"); expect(host.textContent).not.toContain("続きを読む");
      expect(document.activeElement?.textContent).toContain("文字を取得しました");
      expect(load.mock.calls.map(([request]) => request.firstPart)).toEqual([0, 1]);
      expect(load.mock.calls.every(([request]) => request.spanId === "span-a" && request.slot === "input")).toBe(true);
    } finally {dispose();}
  });
  it("pending、不完全の実際の理由、通信障害を区別して再試行する", async () => {
    const {host, root, dispose} = fixture();
    const load = vi.fn<CallBodyLoader>()
      .mockResolvedValueOnce({...page(0), status: "pending", parts: [], next_part: null})
      .mockResolvedValueOnce({...page(0), status: "incomplete", failure_code: "queue_full", parts: [], next_part: null})
      .mockRejectedValueOnce(new Error("synthetic failure")).mockResolvedValueOnce(page(0));
    try {
      act(() => root.render(<RunStepDetailsView details={{call}} activeTab="input" spanId="span-a" loadCallBody={load} />));
      await button(host, "保存済みの全文を読む"); expect(host.textContent).toContain("保存はまだ完了していません");
      await button(host, "再試行"); expect(host.textContent).toContain("この記録は不完全です");
      expect(host.textContent).toContain("待ち行列が上限に達しました");
      await button(host, "保存状態を確認する"); expect(host.textContent).toContain("全文を取得できませんでした");
      await button(host, "再試行"); expect(host.textContent).toContain("続きを読む");
    } finally {dispose();}
  });
  it("権限失効後は取得済み本文も表示し続けない", async () => {
    const {host, root, dispose} = fixture();
    const load = vi.fn<CallBodyLoader>().mockResolvedValueOnce(page(0)).mockRejectedValueOnce({status: 403});
    try {
      act(() => root.render(<RunStepDetailsView details={{call}} activeTab="input" spanId="span-a" loadCallBody={load} />));
      await button(host, "保存済みの全文を読む"); await button(host, "続きを読む");
      expect(host.textContent).toContain("現在の権限では"); expect(host.textContent).not.toContain("preview text");
      expect(host.textContent).not.toContain(pieces[0]);
    } finally {dispose();}
  });
  it("選択変更で旧要求を中止し、遅着した別spanの全文を出さない", async () => {
    const {host, root, dispose} = fixture();
    let resolve: (value: unknown) => void = () => {};
    const load = vi.fn<CallBodyLoader>().mockImplementation(() => new Promise(done => {resolve = done;}));
    try {
      act(() => root.render(<RunStepDetailsView details={{call}} activeTab="input" spanId="span-a" loadCallBody={load} />));
      await button(host, "保存済みの全文を読む");
      act(() => root.render(<RunStepDetailsView details={{call}} activeTab="input" spanId="span-b" loadCallBody={load} />));
      expect(load.mock.calls[0][0].signal.aborted).toBe(true);
      await act(async () => {resolve(page(0));});
      expect(host.textContent).toContain("preview text"); expect(host.textContent).not.toContain("続きを読む");
    } finally {dispose();}
  });
});
