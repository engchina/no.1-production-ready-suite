/* eslint-disable no-restricted-syntax -- adherence の規則のテスト。検出させる違反の例を文字列で持つため。 */
import { ESLint } from "eslint";
import { describe, expect, it } from "vitest";

// platform の adherence（デザインシステム遵守ルール）の正本。RAG / Agent は ESLint の no-restricted-syntax に、
// NL2SQL は oxlint の JS プラグインに同じセレクタを渡す（platform AGENTS.md「lint」節）。
import adherence from "../../../platform/docs/design-system/adherence.oxlintrc.json" with { type: "json" };

const LOADING_ICON = "loading を渡す Button には icon を渡す";
const FOCUS_RING = "フォーカスの表示を ring";

async function lint(code: string) {
  const eslint = new ESLint({
    overrideConfigFile: true,
    overrideConfig: [
      {
        files: ["**/*.tsx"],
        languageOptions: {
          parser: (await import("@typescript-eslint/parser")).default,
          parserOptions: { ecmaFeatures: { jsx: true }, ecmaVersion: "latest", sourceType: "module" },
        },
        rules: { "no-restricted-syntax": adherence.overrides[0].rules["design-system/restricted-syntax"] as never },
      },
    ],
  });
  const [result] = await eslint.lintText(code, { filePath: "probe.tsx" });
  return result.messages.map((message) => ({ line: message.line, message: message.message }));
}

function linesWith(messages: { line: number; message: string }[], text: string) {
  return messages.filter((message) => message.message.startsWith(text)).map((message) => message.line);
}

describe("adherence: loading を渡す Button の icon（#355）", () => {
  it("icon の無い loading を検出し、icon あり・spread・loading なしは許す", async () => {
    const messages = await lint(`
const a = <Button loading={busy}>保存</Button>;
const b = <Button loading={busy} icon={Save}>保存</Button>;
const c = <Button {...props} loading={busy}>保存</Button>;
const d = <Button>保存</Button>;
const e = <Button loading={busy} iconOnly icon={X} aria-label="閉じる" />;
const f = <Other loading={busy}>保存</Other>;
`);
    expect(linesWith(messages, LOADING_ICON)).toEqual([2]);
  });
});

describe("adherence: フォーカスの表示は outline（#355）", () => {
  it("focus / focus-visible / focus-within の ring と outline-none を検出する", async () => {
    const messages = await lint(`
const a = <button className="focus:outline-none focus:ring-2">x</button>;
const b = <button className={\`px-2 focus-visible:ring-focus-ring \${x}\`}>x</button>;
const c = <div className="md:focus-within:ring-2">x</div>;
const d = <div className="peer-focus-visible:ring-offset-2">x</div>;
const e = <div className="focus-visible:outline-none">x</div>;
`);
    expect(linesWith(messages, FOCUS_RING)).toEqual([2, 3, 4, 5, 6]);
  });

  it("outline の調整・フォーカス以外の ring・入力欄の outline-none は許す", async () => {
    const messages = await lint(`
const a = <div className="ring-2 ring-accent-emphasis">選択中</div>;
const b = <button className="focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-focus-ring">x</button>;
const c = <input className="outline-none focus:border-focus-ring" />;
const d = <div className="peer-focus-visible:outline-2 focus-within:outline-2">x</div>;
const e = "string with focus ring words";
`);
    expect(linesWith(messages, FOCUS_RING)).toEqual([]);
  });
});
