import assert from "node:assert/strict";
import test from "node:test";

import { QUESTION_TEMPLATES } from "../src/features/nl2sql/questionTemplates.ts";

test("すべてのテンプレートが対象テーブル行を持つ穴埋め形式である", () => {
  assert.equal(QUESTION_TEMPLATES.length, 4);
  for (const template of QUESTION_TEMPLATES) {
    assert.match(template.body, /^対象テーブル/);
    assert.match(template.body, /抽出項目：|集計内容/);
    assert.ok(template.body.includes("抽出条件："));
    // 全行が「ラベル：」形式(値は空欄)
    for (const line of template.body.split("\n")) {
      assert.match(line, /：$/);
    }
  }
});

test("ラベルキーが重複せず nl2sql.question.template 名前空間に属する", () => {
  const keys = QUESTION_TEMPLATES.map((template) => template.labelKey);
  assert.equal(new Set(keys).size, keys.length);
  for (const key of keys) {
    assert.match(key, /^nl2sql\.question\.template\./);
  }
});
