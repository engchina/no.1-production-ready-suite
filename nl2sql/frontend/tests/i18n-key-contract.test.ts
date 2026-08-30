import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, relative, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { t } from "../src/lib/i18n.ts";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const sourceRoot = resolve(frontendRoot, "src");
const dictionaryPaths = [
  resolve(sourceRoot, "lib/i18n.ts"),
  resolve(sourceRoot, "lib/nl2sql-base-i18n.ts"),
];

function sourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    return /\.tsx?$/u.test(entry.name) ? [path] : [];
  });
}

function lineNumber(source: string, index: number): number {
  return source.slice(0, index).split("\n").length;
}

test("静的な i18n key はすべて日本語辞書に定義されている", () => {
  const definedKeys = new Set(
    dictionaryPaths.flatMap((path) =>
      Array.from(readFileSync(path, "utf8").matchAll(/^\s*"([^"]+)"\s*:/gmu), (match) =>
        match[1]
      )
    )
  );
  const missing = sourceFiles(sourceRoot).flatMap((path) => {
    const source = readFileSync(path, "utf8");
    return Array.from(source.matchAll(/\bt\(\s*"([^"]+)"/gu))
      .filter((match) => !definedKeys.has(match[1]))
      .map(
        (match) =>
          `${match[1]} (${relative(frontendRoot, path)}:${lineNumber(source, match.index)})`
      );
  });

  assert.equal(
    missing.length,
    0,
    `日本語辞書に未定義の i18n key があります:\n${missing.join("\n")}`
  );
});

test("共通・スキーマ読込状態は利用者向けの日本語ラベルを返す", () => {
  assert.equal(t("common.loading"), "読み込んでいます");
  assert.equal(t("common.processing.slow"), "通常より時間がかかっています。");
  assert.equal(t("nl2sql.schema.loading"), "スキーマ情報を読み込んでいます");
});

test("一括選択は範囲が明確な共通文言を返す", () => {
  assert.equal(t("common.selection.selectAll"), "すべて選択");
  assert.equal(t("common.selection.clearAll"), "選択をすべて解除");
  assert.equal(t("common.selection.selectVisible"), "表示中をすべて選択");
  assert.equal(t("common.selection.clearVisible"), "表示中の選択をすべて解除");
  assert.equal(t("common.selection.selectGroup", { name: "APP" }), "APP をすべて選択");
  assert.equal(
    t("common.selection.clearGroup", { name: "APP" }),
    "APP の選択をすべて解除"
  );
  assert.equal(t("profiles.objects.selectSchemaAction"), "すべて選択");
  assert.equal(t("profiles.objects.clearSchema"), "選択をすべて解除");
  assert.equal(t("knowledgeBasePicker.selectAll"), "すべて選択");
  assert.equal(t("knowledgeBasePicker.clear"), "選択をすべて解除");
});
