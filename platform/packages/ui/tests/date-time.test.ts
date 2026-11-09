import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  DEFAULT_DISPLAY_TIME_ZONE,
  displayDateTimeFormat,
  displayDateTimeParts,
  displayFileTimestamp,
  displayTimeZone,
  formatDisplayCalendarDate,
  formatDisplayDateTime,
  isSupportedTimeZone,
  operationTimestampMs,
  parseApiDateTime,
  setDisplayTimeZone,
} from "../src";

// 東京では翌日の 0 時台、UTC ではその前日（日付が変わる境目で確かめる）。
const MOMENT = "2026-10-24T15:30:05.000Z";

afterEach(() => setDisplayTimeZone(null));

describe("表示の時間帯（#3265）", () => {
  it("既定は UTC で、端末の時間帯に従わない（#3274）", () => {
    expect(DEFAULT_DISPLAY_TIME_ZONE).toBe("UTC");
    expect(displayTimeZone()).toBe("UTC");
    expect(formatDisplayDateTime(MOMENT, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })).toBe(
      "10/24 15:30",
    );
    setDisplayTimeZone("Asia/Tokyo");
    expect(formatDisplayDateTime(MOMENT, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })).toBe(
      "10/25 00:30",
    );
  });

  it("設定した時間帯で書き、読めない値・空は既定に戻す", () => {
    setDisplayTimeZone("UTC");
    expect(displayTimeZone()).toBe("UTC");
    expect(formatDisplayDateTime(MOMENT, { year: "numeric", month: "2-digit", day: "2-digit" })).toBe("2026/10/24");
    setDisplayTimeZone("America/New_York");
    expect(formatDisplayDateTime(MOMENT, { hour: "2-digit", minute: "2-digit" })).toBe("11:30");
    setDisplayTimeZone("Not/AZone");
    expect(displayTimeZone()).toBe("UTC");
    setDisplayTimeZone("Asia/Tokyo");
    setDisplayTimeZone("");
    expect(displayTimeZone()).toBe("UTC");
  });

  it("読めない日時は invalid の文字", () => {
    expect(formatDisplayDateTime("not-a-date", { hour: "2-digit" })).toBe("—");
    expect(formatDisplayDateTime("not-a-date", { hour: "2-digit" }, "-")).toBe("-");
    expect(displayDateTimeParts("not-a-date")).toBeNull();
  });

  it("年月日時分秒とファイル名の時刻は表示の時間帯（0 時は 0）", () => {
    setDisplayTimeZone("Asia/Tokyo");
    expect(displayDateTimeParts(MOMENT)).toEqual({ year: 2026, month: 10, day: 25, hour: 0, minute: 30, second: 5 });
    expect(displayFileTimestamp(MOMENT)).toBe("20261025-003005");
    setDisplayTimeZone("UTC");
    expect(displayFileTimestamp(MOMENT)).toBe("20261024-153005");
  });

  it("IANA の地域名だけを受ける", () => {
    expect(isSupportedTimeZone("Asia/Tokyo")).toBe(true);
    expect(isSupportedTimeZone("UTC")).toBe(true);
    expect(isSupportedTimeZone("")).toBe(false);
    expect(isSupportedTimeZone("Not/AZone")).toBe(false);
  });

  it("暦の日付は時間帯で前後の日にしない。日時は表示の時間帯", () => {
    const options = { year: "numeric", month: "2-digit", day: "2-digit", weekday: "short" } as const;
    for (const zone of ["Asia/Tokyo", "UTC", "America/Los_Angeles"]) {
      setDisplayTimeZone(zone);
      expect(formatDisplayCalendarDate("2026-10-25", options)).toBe("2026/10/25(日)");
    }
    setDisplayTimeZone("Asia/Tokyo");
    expect(formatDisplayCalendarDate(MOMENT, options)).toBe("2026/10/25(日)");
    expect(formatDisplayCalendarDate("2026-13-40", options)).toBe("—");
  });
});

describe("時間帯の無い API の日時（#4753）", () => {
  // Oracle の TIMESTAMP を印なしで返す API の形（保存は UTC）。端末の時間帯に引きずられないことを確かめる。
  const NAIVE = "2026-11-09T14:15:57.223334";
  const UTC_MS = Date.UTC(2026, 10, 9, 14, 15, 57, 223);

  beforeEach(() => {
    vi.stubEnv("TZ", "Asia/Tokyo");
  });
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("印の無い ISO の日時は端末の時間帯によらず UTC として読む", () => {
    expect(parseApiDateTime(NAIVE)?.getTime()).toBe(UTC_MS);
    expect(displayDateTimeParts(NAIVE)).toMatchObject({ day: 9, hour: 14, minute: 15, second: 57 });
    expect(operationTimestampMs(NAIVE)).toBe(UTC_MS);
  });

  it("表示の時間帯（設定）だけで書き、端末の時間帯は見ない", () => {
    const format = displayDateTimeFormat({ hour: "2-digit", minute: "2-digit" });
    expect(format.format(NAIVE)).toBe("14:15");
    expect(formatDisplayDateTime(NAIVE, { hour: "2-digit", minute: "2-digit" })).toBe("14:15");
    setDisplayTimeZone("Asia/Tokyo");
    expect(format.format(NAIVE)).toBe("23:15");
  });

  it("時間帯のある値・暦の日付・読めない値は変えない", () => {
    expect(parseApiDateTime("2026-11-09T14:15:57Z")?.getTime()).toBe(Date.UTC(2026, 10, 9, 14, 15, 57));
    expect(parseApiDateTime("2026-11-09T23:15:57+09:00")?.getTime()).toBe(Date.UTC(2026, 10, 9, 14, 15, 57));
    expect(parseApiDateTime("2026-11-09")?.getTime()).toBe(Date.UTC(2026, 10, 9));
    expect(parseApiDateTime("not-a-date")).toBeNull();
    expect(parseApiDateTime("")).toBeNull();
    expect(parseApiDateTime(null)).toBeNull();
    expect(parseApiDateTime(undefined)).toBeNull();
  });
});
