import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  SCHEMA_OPTION_ROW_HEIGHT,
  SCHEMA_OPTION_VIEWPORT_HEIGHT,
  schemaOptionWindow,
} from "../src/features/nl2sql/profileVirtualList.ts";

const profilePage = readFileSync(
  new URL("../src/features/nl2sql/pages/ProfileManagementPage.tsx", import.meta.url),
  "utf8",
);

test("スペーサ高さは行高 × 件数と一致する", () => {
  const { totalHeight } = schemaOptionWindow(0, 200);
  assert.equal(totalHeight, 200 * SCHEMA_OPTION_ROW_HEIGHT);
});

test("描画ブロックの offset は行高の整数倍で、start 行の実位置と一致する", () => {
  for (const scrollTop of [0, 100, 520, 1000, 5000]) {
    const { start, offset } = schemaOptionWindow(scrollTop, 500);
    assert.equal(offset, start * SCHEMA_OPTION_ROW_HEIGHT);
  }
});

test("描画範囲はビューポートを常に覆う", () => {
  const count = 500;
  for (let scrollTop = 0; scrollTop <= count * SCHEMA_OPTION_ROW_HEIGHT; scrollTop += 37) {
    const { start, end, offset } = schemaOptionWindow(scrollTop, count);
    const blockTop = offset;
    const blockBottom = offset + (end - start) * SCHEMA_OPTION_ROW_HEIGHT;
    const viewportBottom = Math.min(
      scrollTop + SCHEMA_OPTION_VIEWPORT_HEIGHT,
      count * SCHEMA_OPTION_ROW_HEIGHT,
    );
    assert.ok(blockTop <= scrollTop, `top ${blockTop} > ${scrollTop}`);
    assert.ok(blockBottom >= viewportBottom, `bottom ${blockBottom} < ${viewportBottom}`);
  }
});

test("末尾を超える範囲は返さない", () => {
  const { start, end } = schemaOptionWindow(10_000, 60);
  assert.ok(end <= 60);
  assert.ok(start <= end);
});

test("負値や NaN の scrollTop でも先頭から描画する", () => {
  assert.equal(schemaOptionWindow(-100, 60).start, 0);
  assert.equal(schemaOptionWindow(Number.NaN, 60).start, 0);
});

test("行は共有定数で高さを固定し、仮想スクロールに padding を挟まない", () => {
  assert.match(profilePage, /style=\{\{ height: SCHEMA_OPTION_ROW_HEIGHT \}\}/u);
  assert.match(profilePage, /schemaOptionWindow\(scrollTop, entries\.length\)/u);
  assert.doesNotMatch(profilePage, /const rowHeight = 52;/u);
  assert.doesNotMatch(profilePage, /className="absolute inset-x-0 top-0 grid p-1"/u);
});
