// @vitest-environment happy-dom
import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it } from "vitest";
import { ChatProgress, RunTimelineSection, TracePartialNotice } from "../src";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
function check(node: ReactNode, assertions: (host: HTMLDivElement) => void) {
  const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
  try { act(() => root.render(node)); assertions(host); }
  finally { act(() => root.unmount()); host.remove(); }
}
describe("処理記録の不完全の原因", () => {
  it("読取上限と書込欠落が同時にあっても、二つの案内を残す", () => check(<TracePartialNotice partial readTruncated droppedSpans={7} testId="partial" />, host => {
    expect(host.querySelector('[data-testid="partial-read-truncated"]')?.textContent).toContain("取得件数の上限");
    expect(host.querySelector('[data-testid="partial-write-dropped"]')?.textContent).toBe("一部の処理だけを記録しています。");
  }));
  it("readerだけをwriterの欠落と誤表示しない", () => check(<TracePartialNotice partial readTruncated droppedSpans={0} testId="partial" />, host => {
    expect(host.querySelector('[data-testid="partial-read-truncated"]')).not.toBeNull();
    expect(host.querySelector('[data-testid="partial-write-dropped"]')).toBeNull();
  }));
  it("完了済みでspanがなくてもチャットの書込欠落を表示する", () => check(<ChatProgress steps={[]} active={false} partial droppedSpans={7} testId="chat" />, host => {
    expect(host.querySelector('[data-testid="chat-partial"]')?.textContent).toBe("一部の処理だけを記録しています。");
  }));
  it("空の実行履歴にも欠落を表示し、工程を作らない", () => check(<RunTimelineSection steps={[]} empty="処理の記録がありません。" defaultOpen partial droppedSpans={7} testId="run" />, host => {
    expect(host.querySelector('[data-testid="run-partial"]')?.textContent).toBe("一部の処理だけを記録しています。");
    expect(host.querySelectorAll("[data-run-step-summary]")).toHaveLength(0);
  }));
});

it("読取と書込の明示値だけでも、union partialを要求せず案内を出す", () => {
  check(<ChatProgress steps={[]} active={false} readTruncated testId="reader" />, host => {
    expect(host.querySelector('[data-testid="reader-partial"]')?.textContent).toContain("取得件数の上限");
  });
  check(<TracePartialNotice droppedSpans={7} testId="writer" />, host => {
    expect(host.querySelector('[data-testid="writer"]')?.textContent).toBe("一部の処理だけを記録しています。");
  });
});

it("旧ラベルproviderにreader文言がなくてもwriter理由へ読み替えない", async () => {
  const { DEFAULT_TRACE_DETAILS_LABELS, TraceDetailsLabelsProvider } = await import("../src");
  check(<TraceDetailsLabelsProvider labels={{...DEFAULT_TRACE_DETAILS_LABELS, readTruncated:undefined}}><TracePartialNotice readTruncated testId="reader" /></TraceDetailsLabelsProvider>, host => {
    expect(host.querySelector('[data-testid="reader"]')?.textContent).toContain("取得件数の上限");
    expect(host.textContent).not.toContain("一部の処理だけを記録しています。");
  });
});
