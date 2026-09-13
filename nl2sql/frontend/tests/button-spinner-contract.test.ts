import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";

const srcDir = fileURLToPath(new URL("../src", import.meta.url));
const databaseSettingsSource = readFileSync(
  new URL("../src/components/settings/DatabaseSettingsClient.tsx", import.meta.url),
  "utf8"
);

function collectSourceFiles(dir: string): string[] {
  const files: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = `${dir}/${entry.name}`;
    if (entry.isDirectory()) {
      files.push(...collectSourceFiles(path));
    } else if (entry.name.endsWith(".tsx") || entry.name.endsWith(".ts")) {
      files.push(path);
    }
  }
  return files;
}

/**
 * 回転スピナーの規約。
 *
 * lucide の `Loader2`(loader-circle) は欠けた円弧しか描かないため、回転するとシルエットが角度ごとに動き、
 * 中心がずれて見える。処理中の表示は共有 UI パッケージの `Spinner`（全周トラック付き）と
 * `Button` の `loading`（先頭アイコンをスピナーに置換）に統一する。
 */
test("アプリ内で Loader2 を回さない", () => {
  const offenders = collectSourceFiles(srcDir)
    .filter((file) => readFileSync(file, "utf8").includes("Loader2"))
    .map((file) => file.slice(srcDir.length + 1));
  assert.deepEqual(offenders, [], "処理中の表示は共有 Spinner か Button の loading を使うこと");
});

test("animate-spin を手書きしない（回転は共有 Spinner が持つ）", () => {
  const offenders = collectSourceFiles(srcDir)
    .filter((file) => readFileSync(file, "utf8").includes("animate-spin"))
    .map((file) => file.slice(srcDir.length + 1));
  assert.deepEqual(offenders, []);
});

test("独自のローディングアイコンを持たない", () => {
  const offenders = collectSourceFiles(srcDir)
    .filter((file) => /StableLoadingIcon/u.test(readFileSync(file, "utf8")))
    .map((file) => file.slice(srcDir.length + 1));
  assert.deepEqual(offenders, []);
});

test("raw icon-only button の処理中表示も共有 Spinner を使う", () => {
  assert.match(databaseSettingsSource, /Spinner/u);
  assert.match(databaseSettingsSource, /revealPending \? \(\s*<Spinner size=\{16\} \/>/u);
});
