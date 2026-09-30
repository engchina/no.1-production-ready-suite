/* eslint-disable no-restricted-syntax -- adherence の規則のテスト。検出させる違反の例を文字列で持つため。 */
import { ESLint } from "eslint";
import { describe, expect, it } from "vitest";

// platform の adherence（デザインシステム遵守ルール）の正本。RAG / Agent は ESLint の no-restricted-syntax に、
// NL2SQL は oxlint の JS プラグインに同じセレクタを渡す（platform AGENTS.md「lint」節）。
import adherence from "../../../platform/docs/design-system/adherence.oxlintrc.json" with { type: "json" };

const LOADING_ICON = "loading を渡す Button には icon を渡す";
const FOCUS_RING = "フォーカスの表示を ring";
const HANDWRITTEN_SEARCH = "アイコン付きの入力欄（検索欄）を手書きしない";
const HANDWRITTEN_DISCLOSURE = "開閉できる領域は <details> / <summary> を手書きせず";
const OPTIONAL_MARKER = "任意の欄を「(任意)」や placeholder で示さない";
const HANDWRITTEN_REQUIRED = "必須の表示を手書きしない";

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

describe("adherence: 手書きの検索欄（#384）", () => {
  it("アイコンの分の左の余白（pl-7〜12 / ps-* / 任意値）を持つ input を検出する", async () => {
    const messages = await lint(`
const a = <input className="h-9 w-full rounded-md border pl-9 pr-3" />;
const b = <input className={\`\${INPUT_CLASS} pl-9\`} />;
const c = <input className={cn("w-full", "pl-8")} />;
const d = <input className="md:pl-10" />;
const e = <input className="ps-9" />;
const f = <input className="pl-[2.25rem]" />;
`);
    expect(linesWith(messages, HANDWRITTEN_SEARCH)).toEqual([2, 3, 4, 5, 6, 7]);
  });

  it("TextField・小さい余白の input・input 以外は許す", async () => {
    const messages = await lint(`
const a = <TextField id="q" label="検索" leadingIcon={Search} />;
const b = <input className="px-3 pl-2" />;
const c = <div className="pl-9">入れ子の行</div>;
const d = <textarea className="pl-3" />;
`);
    expect(linesWith(messages, HANDWRITTEN_SEARCH)).toEqual([]);
  });
});

describe("adherence: 手書きの開閉（#397）", () => {
  it("<details> を検出し、Disclosure・DisclosureChevron・details 以外は許す", async () => {
    const messages = await lint(`
const a = <details><summary>詳細</summary>本文</details>;
const b = <details open={open} className="group/disclosure"><summary className="flex">詳細</summary></details>;
const c = <Disclosure summary="詳細">本文</Disclosure>;
const d = <button aria-expanded={open}>詳細<DisclosureChevron expanded={open} /></button>;
const e = <div data-details="x">詳細</div>;
`);
    expect(linesWith(messages, HANDWRITTEN_DISCLOSURE)).toEqual([2, 3]);
  });
});

describe("adherence: 任意の表示（#531）", () => {
  it("placeholder の「任意」と、ラベル・文言の「(任意)」「（任意）」を検出する", async () => {
    const messages = await lint(`
const a = <TextField id="m" label="メモ" placeholder="メモ(任意)" />;
const b = <textarea placeholder={\`説明（任意）\`} />;
const c = { "businessViews.field.descriptionPlaceholder": "この業務ビューの用途(任意)" };
const d = { descriptionPlaceholder: "任意で入力します" };
const e = { "feedback.controls.commentLabel": "コメント（任意）" };
const f = <label htmlFor="x">許可表(任意・カンマ区切り)</label>;
const g = { "settings.huggingface.field.token": "ダウンロード token( 任意 )" };
`);
    expect(linesWith(messages, OPTIONAL_MARKER)).toEqual([2, 3, 4, 5, 6, 7, 8]);
  });

  it("任意の意味の説明文・データの値・任意の欄の placeholder の例は許す", async () => {
    const messages = await lint(`
const a = { "settings.prompts.list.description": "任意の版を有効化(rollback)できます。" };
const b = { "ontologyResults.field.optional": "任意（Optional）" };
const c = <TextField id="m" label="メモ" placeholder="例: 月次の締め処理" />;
const d = { "evaluation.analyze.description": "任意の SQL を確認します。" };
`);
    expect(linesWith(messages, OPTIONAL_MARKER)).toEqual([]);
  });
});

describe("adherence: 手書きの必須表示（#531）", () => {
  it("「*」・「必須」の手書きと RequiredBadge の直接の使用を検出する", async () => {
    const messages = await lint(`
const a = <label htmlFor="n">名前 <span className="text-danger-fg">*</span></label>;
const b = <label htmlFor="n">名前 *</label>;
const c = <label htmlFor="n">名前{" *"}</label>;
const d = <span className="rounded-full">必須</span>;
const e = <StatusBadge variant="warning" label="必須" />;
const f = <label htmlFor="n">名前<RequiredBadge label={t("common.required")} /></label>;
const g = <span className="text-xs">{t("common.required")}</span>;
const h = <Badge label={t("settings.database.requiredMark")} />;
const i = <label htmlFor="n">名前{"※必須"}</label>;
`);
    expect(linesWith(messages, HANDWRITTEN_REQUIRED)).toEqual([2, 3, 4, 5, 6, 7, 8, 9, 10]);
  });

  it("共有部品の required・requiredLabel の上書き・エラー文言・SQL の * は許す", async () => {
    const messages = await lint(`
const a = <TextField id="n" label="名前" required />;
const b = <TextField id="r" label="リージョン" required requiredLabel="OCI 運用時必須" />;
const c = <SelectField id="s" label="種別" required requiredLabel={t("common.required")} />;
const d = <FieldLabel htmlFor="sql" label="SQL" required />;
const e = <Fieldset legend="ロール" required>…</Fieldset>;
const f = <FieldError id="e" message={t("profiles.error.nameRequired")} />;
const g = <p>{t("businessViews.scope.required")}</p>;
const h = <code>SELECT * FROM t</code>;
const i = { scope_code: "*" };
const j = <Route path="*" element={<Home />} />;
`);
    expect(linesWith(messages, HANDWRITTEN_REQUIRED)).toEqual([]);
  });
});
