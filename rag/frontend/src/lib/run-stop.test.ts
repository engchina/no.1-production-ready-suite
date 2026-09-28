import { describe, expect, it } from "vitest";

import { isRepeatedActivationKey, runStopClickAction } from "./run-stop";

describe("runStopClickAction", () => {
  it("待機中の 1 回のクリックとキーボードの暗黙のクリックは実行する", () => {
    expect(runStopClickAction({ running: false, clickCount: 1 })).toBe("run");
    expect(runStopClickAction({ running: false, clickCount: 0 })).toBe("run");
  });

  it("実行中の 1 回のクリックとキーボードの暗黙のクリックは停止する", () => {
    expect(runStopClickAction({ running: true, clickCount: 1 })).toBe("stop");
    expect(runStopClickAction({ running: true, clickCount: 0 })).toBe("stop");
  });

  it("ダブルクリックの 2 回目以降は、実行中でも停止しない", () => {
    expect(runStopClickAction({ running: true, clickCount: 2 })).toBe("ignore");
    expect(runStopClickAction({ running: true, clickCount: 3 })).toBe("ignore");
    expect(runStopClickAction({ running: false, clickCount: 2 })).toBe("ignore");
  });

  it("実行できない間は何もしない。実行中の停止は実行できない条件に左右されない", () => {
    expect(runStopClickAction({ running: false, runDisabled: true, clickCount: 1 })).toBe("ignore");
    expect(runStopClickAction({ running: true, runDisabled: true, clickCount: 1 })).toBe("stop");
  });
});

describe("isRepeatedActivationKey", () => {
  it("押し続けた Enter / Space の繰り返しだけを止める", () => {
    expect(isRepeatedActivationKey({ key: "Enter", repeat: true })).toBe(true);
    expect(isRepeatedActivationKey({ key: " ", repeat: true })).toBe(true);
    expect(isRepeatedActivationKey({ key: "Enter", repeat: false })).toBe(false);
    expect(isRepeatedActivationKey({ key: "Enter" })).toBe(false);
    expect(isRepeatedActivationKey({ key: "Tab", repeat: true })).toBe(false);
  });
});
