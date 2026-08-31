import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";

const globalsCss = readFileSync(new URL("../src/globals.css", import.meta.url), "utf8");
const srcDir = fileURLToPath(new URL("../src", import.meta.url));
const buttonSource = readFileSync(
  new URL("../src/components/ui/button.tsx", import.meta.url),
  "utf8"
);
const stableLoadingIconSource = readFileSync(
  new URL("../src/components/ui/stable-loading-icon.tsx", import.meta.url),
  "utf8"
);
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
 * lucide の `Loader2`(loader-circle) は 288 度の欠けた円弧しか描かないため、回転すると
 * インクの重心とシルエットが角度ごとに動き、「中心がずれて上下に揺れている」ように見える。
 * ボタン内は 180 度対称の `StableLoadingIcon` に統一し、見た目の重心を中央に保つ。
 */
test("アプリ内のボタン loading は StableLoadingIcon に統一する（Loader2 を直接回さない）", () => {
  const offenders: string[] = [];
  for (const file of collectSourceFiles(srcDir)) {
    const source = readFileSync(file, "utf8");
    if (!source.includes("Loader2")) continue;
    offenders.push(file.slice(srcDir.length + 1));
  }
  assert.deepEqual(
    offenders,
    [],
    "Loader2 を回すとシルエットが角度ごとに変わる。ボタン内は StableLoadingIcon を使うこと",
  );
});

test("animate-spin を付ける要素は StableLoadingIcon に集約する", () => {
  const offenders: string[] = [];
  for (const file of collectSourceFiles(srcDir)) {
    const relative = file.slice(srcDir.length + 1);
    if (relative === "components/ui/stable-loading-icon.tsx") continue;
    const source = readFileSync(file, "utf8");
    if (!source.includes("animate-spin")) continue;
    offenders.push(relative);
  }
  // 回転 animation の所有者を 1 箇所に絞り、ボタンごとの手書き spinner を防ぐ。
  assert.deepEqual(offenders, []);
});

test("アプリ内では共有 Spinner を直接 import しない", () => {
  const offenders: string[] = [];
  for (const file of collectSourceFiles(srcDir)) {
    const source = readFileSync(file, "utf8");
    if (!source.includes("Spinner")) continue;
    if (!source.includes("from \"@engchina/production-ready-ui\"")) continue;
    offenders.push(file.slice(srcDir.length + 1));
  }
  assert.deepEqual(
    offenders,
    [],
    "処理中 icon はアプリ内の StableLoadingIcon に統一すること",
  );
});

test("StableLoadingIcon は 180 度対称の active arc を持つ", () => {
  assert.match(stableLoadingIconSource, /data-loading-icon="true"/u);
  assert.match(stableLoadingIconSource, /data-loading-icon-track="true"/u);
  assert.match(stableLoadingIconSource, /r="8\.25"/u);
  assert.match(stableLoadingIconSource, /d="M12 3\.75A8\.25 8\.25 0 0 1 20\.25 12"/u);
  assert.match(stableLoadingIconSource, /d="M12 20\.25A8\.25 8\.25 0 0 1 3\.75 12"/u);
  assert.equal(
    stableLoadingIconSource.match(/data-loading-icon-active="true"/gu)?.length,
    2
  );
});

test("ローカル Button は共有 BaseButton の loading 表示を使わず StableLoadingIcon を描画する", () => {
  assert.match(buttonSource, /import \{ StableLoadingIcon \} from "\.\/stable-loading-icon"/u);
  assert.match(buttonSource, /const \{ loading, disabled, children, \.\.\.buttonProps \} = props/u);
  assert.match(buttonSource, /disabled=\{disabled \|\| loading\}/u);
  assert.match(buttonSource, /<StableLoadingIcon size=\{16\} \/>/u);
  assert.match(buttonSource, /\[&>svg:not\(\[data-loading-icon\]\)\]:hidden/u);
  assert.doesNotMatch(buttonSource, /<BaseButton[\s\S]*loading=\{/u);
});

test("raw icon-only button の処理中表示も StableLoadingIcon を使う", () => {
  assert.match(databaseSettingsSource, /import \{ StableLoadingIcon \} from "@\/components\/ui\/stable-loading-icon"/u);
  assert.match(databaseSettingsSource, /revealPending \? \(\s*<StableLoadingIcon size=\{16\} \/>/u);
  assert.doesNotMatch(databaseSettingsSource, /import \{ Spinner/u);
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
