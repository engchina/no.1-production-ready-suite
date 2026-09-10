import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import test from "node:test";
import ts from "typescript";

const root = new URL("../src/", import.meta.url).pathname;
function files(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap(entry =>
    entry.isDirectory() ? files(join(dir, entry.name)) : entry.name.endsWith(".tsx") ? [join(dir, entry.name)] : []);
}

test("アクションはアプリ共通 Button を使い、raw button は選択・ナビ・入力部品に限定する", () => {
  const violations: string[] = [];
  for (const file of files(root)) {
    const path = relative(root, file);
    if (path === "components/ui/button.tsx") continue;
    const source = ts.createSourceFile(file, readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    function visit(node: ts.Node) {
      if (ts.isImportDeclaration(node) && node.moduleSpecifier.getText(source).includes("@engchina/production-ready-ui")) {
        const imports = node.importClause?.namedBindings;
        if (imports && ts.isNamedImports(imports) && imports.elements.some(item =>
          ["Button", "buttonVariants", "Pagination", "ErrorState", "Toaster", "ToggleChip"].includes((item.propertyName ?? item.name).text))) {
          violations.push(`${path}: action component bypasses local Button`);
        }
      }
      if ((ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) && node.tagName.getText(source) === "button") {
        const attributes = node.attributes.getText(source);
        const structuralControl = /role="(?:tab|combobox|option|listitem)"|aria-current=|aria-pressed=|onSelect(?:Node|Edge)?(?:\?\.)?\(/u.test(attributes)
          || (path === "components/SortHeader.tsx" && attributes.includes('data-sort-header="true"'));
        if (!structuralControl) violations.push(`${path}: raw action button`);
      }
      ts.forEachChild(node, visit);
    }
    visit(source);
  }
  assert.deepEqual(violations, []);
});
