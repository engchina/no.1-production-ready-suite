import { afterEach, describe, expect, it, vi } from "vitest";

import { browserTimezone, isValidTimezone } from "./timezones";

afterEach(() => vi.restoreAllMocks());

describe("isValidTimezone", () => {
  it.each(["UTC", "Asia/Tokyo", "America/New_York", "+00:00", "+09:00", "+05:30", "-03:30", "+23:59", ""])("%s を受け付ける", (value) => {
    expect(isValidTimezone(value)).toBe(true);
  });
  it.each(["Invalid/Zone", "+24:00", "+09:60", "+9:00", "09:00"])("%s を拒否する", (value) => {
    expect(isValidTimezone(value)).toBe(false);
  });
});

describe("browserTimezone", () => {
  it.each([
    ["UTC", "UTC"], ["Asia/Tokyo", "Asia/Tokyo"], ["+00:00", "+00:00"],
    ["+09:00", "+09:00"], ["Invalid/Zone", "Asia/Tokyo"], ["", "Asia/Tokyo"],
  ])("%s は %s を返す", (value, expected) => {
    const options = Intl.DateTimeFormat().resolvedOptions();
    vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({ ...options, timeZone: value });
    expect(browserTimezone()).toBe(expected);
  });
  it("取得できない環境では既定へ戻す", () => {
    vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockImplementation(() => { throw new Error("unavailable"); });
    expect(browserTimezone()).toBe("Asia/Tokyo");
  });
});
