import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";

const globalsCss = readFileSync(new URL("../src/globals.css", import.meta.url), "utf8");
const srcDir = fileURLToPath(new URL("../src", import.meta.url));

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
 * lucide の `Loader2`(loader-circle) は 288 度の欠けた円弧しか描かないため、回転すると
 * インクの重心とシルエットが角度ごとに動き、「中心がずれて上下に揺れている」ように見える。
 * 全周トラックを持つ共有 `Spinner` に統一して、シルエットを回転角によらず一定に保つ。
 */
test("アプリ内のスピナーは共有 Spinner に統一する（Loader2 を直接回さない）", () => {
  const offenders: string[] = [];
  for (const file of collectSourceFiles(srcDir)) {
    const source = readFileSync(file, "utf8");
    if (!source.includes("Loader2")) continue;
    offenders.push(file.slice(srcDir.length + 1));
  }
  assert.deepEqual(
    offenders,
    [],
    "Loader2 を回すとシルエットが角度ごとに変わる。@engchina/production-ready-ui の Spinner を使うこと",
  );
});

test("animate-spin を付ける要素は共有 Spinner 経由にする", () => {
  const offenders: string[] = [];
  for (const file of collectSourceFiles(srcDir)) {
    const source = readFileSync(file, "utf8");
    if (!source.includes("animate-spin")) continue;
    offenders.push(file.slice(srcDir.length + 1));
  }
  // globals.css の規則以外に animate-spin を直書きする箇所は無い
  assert.deepEqual(offenders, []);
});

test("globals.css が回転原点を図形中心へ固定し合成レイヤーで回す", () => {
  const rule = globalsCss.match(/svg\.animate-spin\s*\{[^}]*\}/);
  assert.ok(rule, "svg.animate-spin ルールが globals.css に存在しない");
  assert.match(rule[0], /transform-box:\s*view-box/);
  assert.match(rule[0], /transform-origin:\s*50%\s+50%/);
  assert.match(rule[0], /will-change:\s*transform/);
});

test("globals.css が prefers-reduced-motion でスピナーを止める", () => {
  assert.match(
    globalsCss,
    /@media \(prefers-reduced-motion: reduce\) \{\s*svg\.animate-spin \{\s*animation: none;/,
  );
});
