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
