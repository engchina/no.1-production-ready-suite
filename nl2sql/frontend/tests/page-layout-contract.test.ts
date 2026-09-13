import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import test from "node:test";
import ts from "typescript";

// NL2SQL は全画面が画面幅いっぱい（PageHeader / PageBody の `wide`）。例外を作らない（#566）。
// PageHeader と PageBody の wide がずれると、広い画面でタイトルと本文の左端がずれる。
// 読み・入力の行長は、ページ幅ではなくカード内のフォームや説明文の最大幅で抑える。
const root = new URL("../src/", import.meta.url).pathname;
function files(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) =>
    entry.isDirectory() ? files(join(dir, entry.name)) : entry.name.endsWith(".tsx") ? [join(dir, entry.name)] : [],
  );
}

test("src のすべての PageHeader / PageBody が wide を持つ", () => {
  const violations: string[] = [];
  const counts = { PageHeader: 0, PageBody: 0 };
  for (const file of files(root)) {
    const path = relative(root, file);
    const source = ts.createSourceFile(file, readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    function visit(node: ts.Node) {
      if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
        const tag = node.tagName.getText(source);
        if (tag === "PageHeader" || tag === "PageBody") {
          counts[tag] += 1;
          const wide = node.attributes.properties.find(
            (property): property is ts.JsxAttribute => ts.isJsxAttribute(property) && property.name.getText(source) === "wide",
          );
          // `wide` か `wide={true}` だけを許可する。条件式や `wide={false}` は画面ごとの例外になる。
          const literal = !wide?.initializer || wide.initializer.getText(source) === "{true}";
          if (!wide || !literal) {
            const { line } = source.getLineAndCharacterOfPosition(node.getStart(source));
            violations.push(`${path}:${line + 1} <${tag}> に wide がない`);
          }
        }
      }
      ts.forEachChild(node, visit);
    }
    visit(source);
  }
  assert.deepEqual(violations, []);
  assert.ok(counts.PageHeader >= 20 && counts.PageBody >= 20, `検査対象が見つかる: ${JSON.stringify(counts)}`);
});

test("メモリモードの警告バナーも全幅で、直下の PageHeader と左端をそろえる", () => {
  const gate = readFileSync(new URL("../src/components/system/DatabaseGate.tsx", import.meta.url), "utf8");
  assert.match(gate, /<PageBody wide className="pb-0">/u);
});

test("AI要件確認の未入力案内は操作前に赤いエラー（role=alert）で出さない", () => {
  const workbench = readFileSync(new URL("../src/features/nl2sql/Nl2SqlWorkbench.tsx", import.meta.url), "utf8");
  assert.match(workbench, /<p id="nl2sql-guided-query-required" className="text-xs leading-5 text-fg-muted">/u);
  assert.doesNotMatch(workbench, /<FieldError[\s\S]{0,80}nl2sql-guided-query-required/u);
  assert.match(workbench, /aria-describedby=\{!question\.trim\(\) \? "nl2sql-guided-query-required" : undefined\}/u);
});

test("設定・入力フォームは wide のカード内で読みやすい最大幅に止める", () => {
  const layout = readFileSync(new URL("../src/lib/form-layout.ts", import.meta.url), "utf8");
  assert.match(layout, /READABLE_FORM_WIDTH = "max-w-\[var\(--content-max-width\)\]"/u);
  const forms: Record<string, number> = {
    "components/settings/OciSettingsClient.tsx": 2,
    "components/settings/UploadStorageSettingsClient.tsx": 1,
    "components/settings/ModelSettingsClient.tsx": 3,
    "components/settings/DatabaseSettingsClient.tsx": 3,
    "components/settings/SystemTablesCard.tsx": 1,
    "features/security/SecurityDeepSecPage.tsx": 1,
    "features/nl2sql/pages/EvaluationPage.tsx": 1,
  };
  for (const [path, count] of Object.entries(forms)) {
    const source = readFileSync(new URL(`../src/${path}`, import.meta.url), "utf8");
    assert.equal(source.match(/className=\{`[^`]*\$\{READABLE_FORM_WIDTH\}`\}/gu)?.length ?? 0, count, path);
  }
});
