import assert from "node:assert/strict";
import test from "node:test";

import { formatDateTimeWithYear } from "../src/lib/format.ts";

test("formatDateTimeWithYear は年を含む日本語形式で日時を表示する", () => {
  const formatted = formatDateTimeWithYear("2026-07-19T00:00:00Z");

  assert.match(formatted, /^2026\/07\/19 \d{2}:\d{2}$/);
});

test("formatDateTimeWithYear は未設定または不正な日時をダッシュで表示する", () => {
  assert.equal(formatDateTimeWithYear(null), "—");
  assert.equal(formatDateTimeWithYear("not-a-date"), "—");
});
