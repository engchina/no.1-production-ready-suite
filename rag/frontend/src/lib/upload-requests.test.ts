import { describe, expect, it } from "vitest";

import type { BatchUploadResult } from "./api";
import {
  DEFAULT_MAX_UPLOAD_BYTES,
  failedUploadItem,
  formatUploadLimit,
  mergeBatchUploadResults,
  planUploadRequests,
} from "./upload-requests";

const MB = 1024 * 1024;

function file(name: string, size: number) {
  return { name, size };
}

describe("planUploadRequests", () => {
  it("1 リクエストの合計が 1 ファイルの上限を超えないように分ける", () => {
    const files = [file("a", 120 * MB), file("b", 90 * MB), file("c", 60 * MB), file("d", 10 * MB)];

    const plan = planUploadRequests(files, 200 * MB);

    expect(plan.groups.map((group) => group.map((item) => item.name))).toEqual([["a"], ["b", "c", "d"]]);
    expect(plan.oversized).toEqual([]);
    for (const group of plan.groups) {
      expect(group.reduce((sum, item) => sum + item.size, 0)).toBeLessThanOrEqual(200 * MB);
    }
  });

  it("上限を超えるファイルは送らずに分け、残りは選択した順のまま送る", () => {
    const files = [file("small", 1 * MB), file("huge", 201 * MB), file("exact", 200 * MB)];

    const plan = planUploadRequests(files, 200 * MB);

    expect(plan.oversized.map((item) => item.name)).toEqual(["huge"]);
    expect(plan.groups.map((group) => group.map((item) => item.name))).toEqual([["small"], ["exact"]]);
  });

  it("小さなファイルが多いときはファイル数でも分ける", () => {
    const files = Array.from({ length: 7 }, (_, index) => file(`f${index}`, 1));

    const plan = planUploadRequests(files, 200 * MB, 3);

    expect(plan.groups.map((group) => group.length)).toEqual([3, 3, 1]);
  });

  it("上限が取れないときは backend の既定値で分ける", () => {
    const plan = planUploadRequests([file("a", DEFAULT_MAX_UPLOAD_BYTES + 1)], Number.NaN);

    expect(plan.oversized).toHaveLength(1);
    expect(plan.groups).toEqual([]);
  });
});

describe("mergeBatchUploadResults", () => {
  it("分けて送った結果と送らなかったファイルを 1 つの結果にまとめる", () => {
    const item = (id: string) =>
      ({ id, file_name: `${id}.txt` }) as unknown as BatchUploadResult["items"][number];
    const first: BatchUploadResult = {
      items: [item("a")],
      failed_items: [failedUploadItem({ name: "x.exe" }, 415, "対応していないファイル形式です。")],
      total_count: 2,
      uploaded_count: 1,
      failed_count: 1,
      queued_count: 0,
      skipped_count: 0,
    };
    const second: BatchUploadResult = {
      items: [item("b")],
      failed_items: [],
      total_count: 1,
      uploaded_count: 1,
      failed_count: 0,
      queued_count: 0,
      skipped_count: 0,
    };

    const merged = mergeBatchUploadResults(
      [first, second],
      [failedUploadItem({ name: "huge.pdf" }, 413, "上限超過")],
    );

    expect(merged.items.map((entry) => entry.id)).toEqual(["a", "b"]);
    expect(merged.failed_items.map((entry) => [entry.file_name, entry.status_code])).toEqual([
      ["x.exe", 415],
      ["huge.pdf", 413],
    ]);
    expect(merged).toMatchObject({ total_count: 4, uploaded_count: 2, failed_count: 2 });
  });
});

describe("formatUploadLimit", () => {
  it("MiB 単位の上限を整数で示す", () => {
    expect(formatUploadLimit(200 * MB)).toBe("200 MB");
    expect(formatUploadLimit(1.5 * MB)).toBe("1.5 MB");
    expect(formatUploadLimit(512)).toBe("512 B");
  });
});
