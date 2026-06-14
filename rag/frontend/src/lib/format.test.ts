import { describe, expect, it } from "vitest";

import { formatBytes, formatDateTime, formatNumber } from "./format";

describe("formatBytes", () => {
  it("null は em-dash", () => expect(formatBytes(null)).toBe("—"));
  it("バイト", () => expect(formatBytes(512)).toBe("512 B"));
  it("KB", () => expect(formatBytes(2048)).toBe("2.0 KB"));
  it("MB", () => expect(formatBytes(1048576)).toBe("1.0 MB"));
});

describe("formatNumber", () => {
  it("カンマ区切り", () => expect(formatNumber(38421)).toBe("38,421"));
});

describe("formatDateTime", () => {
  it("無効値は em-dash", () => expect(formatDateTime("not-a-date")).toBe("—"));
  it("ISO を整形する", () => expect(formatDateTime("2026-06-14T10:42:00Z")).not.toBe(""));
});
