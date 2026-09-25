import assert from "node:assert/strict";
import test from "node:test";

import { formatDateTimeWithYear, identifierWrapSegments } from "../src/lib/format.ts";

test("formatDateTimeWithYear は年を含む日本語形式で日時を表示する", () => {
  const formatted = formatDateTimeWithYear("2026-07-19T00:00:00Z");

  assert.match(formatted, /^2026\/07\/19 \d{2}:\d{2}$/);
});

test("formatDateTimeWithYear は未設定または不正な日時をダッシュで表示する", () => {
  assert.equal(formatDateTimeWithYear(null), "—");
  assert.equal(formatDateTimeWithYear("not-a-date"), "—");
});

test("identifierWrapSegments は識別子を . _ $ # の直後で分割し、連結すると元の値に戻る", () => {
  assert.deepEqual(identifierWrapSegments("ADMIN.DENPYO_ACTIVITY_LOG"), ["ADMIN.", "DENPYO_", "ACTIVITY_", "LOG"]);
  assert.deepEqual(identifierWrapSegments("SYS$TAB#1"), ["SYS$", "TAB#", "1"]);
  assert.deepEqual(identifierWrapSegments("ORDERS"), ["ORDERS"]);
  assert.deepEqual(identifierWrapSegments("A__B."), ["A__", "B."]);
  assert.deepEqual(identifierWrapSegments(""), [""]);
  for (const value of ["ADMIN.DENPYO_ACTIVITY_LOG", "_X_", "日本語_テーブル", "A.B.C"]) {
    assert.equal(identifierWrapSegments(value).join(""), value);
  }
});
