import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  ProcessingIndicator,
  TimedLoadingState,
  elapsedMsBetween,
  formatElapsedClock,
  operationTimestampMs,
} from "../src";

describe("operation timing", () => {
  it("1 時間未満は mm:ss、以上は h:mm:ss に固定桁で整形する", () => {
    expect(formatElapsedClock(59_000)).toBe("00:59");
    expect(formatElapsedClock(60_000)).toBe("01:00");
    expect(formatElapsedClock(3_723_000)).toBe("1:02:03");
    expect(formatElapsedClock(-1)).toBe("00:00");
  });

  it("サーバーの時刻から経過時間を求め、読めない時刻は null にする", () => {
    expect(operationTimestampMs("invalid")).toBeNull();
    expect(operationTimestampMs(Number.NaN)).toBeNull();
    expect(elapsedMsBetween("2026-07-29T00:00:00.000Z", "2026-07-29T00:00:01.250Z")).toBe(1250);
    expect(elapsedMsBetween(null, null)).toBeNull();
  });
});

describe("ProcessingIndicator", () => {
  it("実行中は既定の日本語の見出しで経過時間を出し、timer を読み上げない", () => {
    const html = renderToStaticMarkup(<ProcessingIndicator active label="取得しています" testId="p" />);
    expect(html).toContain('aria-busy="true"');
    expect(html).toContain('data-processing-placement="panel"');
    expect(html).toContain('role="timer"');
    expect(html).toContain('aria-live="off"');
    expect(html).toContain("経過時間");
    expect(html).toContain("取得しています");
  });

  it("完了後は所要時間と完了の文言を出し、labels で文言を差し替えられる", () => {
    const html = renderToStaticMarkup(
      <ProcessingIndicator
        active={false}
        label="取得しています"
        elapsedMs={65_000}
        labels={{ duration: "Duration", completed: "Done" }}
      />
    );
    expect(html).toContain("Duration");
    expect(html).toContain("Done");
    expect(html).toContain("01:05");
    expect(html).not.toContain('aria-busy="true"');
  });

  it("activityIcon=none では動くスピナーを出さない", () => {
    const html = renderToStaticMarkup(<ProcessingIndicator active label="x" activityIcon="none" />);
    expect(html).toContain('data-processing-activity-icon="none"');
    expect(html).not.toContain("animate-spin");
  });
});

describe("TimedLoadingState", () => {
  it("result の配置では既定でスピナーを出さず、子要素で寸法を保つ", () => {
    const html = renderToStaticMarkup(
      <TimedLoadingState label="生成しています" placement="result" testId="t">
        <div data-skeleton="" />
      </TimedLoadingState>
    );
    expect(html).toContain('aria-label="生成しています"');
    expect(html).toContain('data-processing-activity-icon="none"');
    expect(html).toContain("data-skeleton");
  });

  it("panel の配置では既定でスピナーを出す", () => {
    const html = renderToStaticMarkup(<TimedLoadingState label="読み込んでいます" />);
    expect(html).toContain('data-processing-activity-icon="spinner"');
    expect(html).toContain("animate-spin");
  });
});
