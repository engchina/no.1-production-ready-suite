import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { extname } from "node:path";
import test from "node:test";

function sourceFiles(directory: URL): URL[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const child = new URL(`${entry.name}${entry.isDirectory() ? "/" : ""}`, directory);
    if (entry.isDirectory()) return sourceFiles(child);
    return [".ts", ".tsx"].includes(extname(entry.name)) ? [child] : [];
  });
}

test("SelectField は共有パッケージから import し、アプリ内に再実装を持たない", () => {
  assert.equal(existsSync(new URL("../src/components/ui/select-field.tsx", import.meta.url)), false);
  const users = sourceFiles(new URL("../src/", import.meta.url))
    .map((file) => readFileSync(file, "utf8"))
    .filter((text) => /<SelectField\b/u.test(text));
  assert.ok(users.length > 0);
  for (const text of users) {
    assert.match(text, /\bSelectField,[\s\S]*\} from "@engchina\/production-ready-ui";/u);
  }
  for (const file of sourceFiles(new URL("../src/", import.meta.url))) {
    assert.doesNotMatch(readFileSync(file, "utf8"), /@\/components\/ui\/(?:select-field|confirm-dialog|toaster)/u, file.pathname);
  }
});
