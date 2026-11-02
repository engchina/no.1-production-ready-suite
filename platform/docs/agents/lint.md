# デザインシステムの lint

> platform/AGENTS.md「lint」の詳細。platform/AGENTS.md の規則と同じ強さで守る。

`docs/design-system/adherence.oxlintrc.json` がデザインシステムの遵守ルールの正本。新しいコードにこの lint を通すことで、デザインシステムからのずれを止める。

- 対象は各製品の `frontend/src/**/*.{ts,tsx}` と `packages/system-settings/src`（`packages/system-settings/.oxlintrc.json`。`npm run lint`。CI は `Platform / UI`。#1891）。検出する規則の一覧と理由は、adherence の各ルールの `message` と README の表（§0）にある。
- 規則のテストは RAG の `frontend/src/design-system-adherence.test.ts`（ESLint の `lintText` で検出と許容の例を確かめる）。規則を足すときは同じテストに例を足す。
- prop の妥当性は TypeScript の型チェックに任せ、lint では検査しない。
- oxlint にはネイティブの `no-restricted-syntax` が無いため、同じ `{selector, message}` を受け取る JS プラグイン `docs/design-system/design-system-plugin.mjs` を置き、adherence 設定から相対パスで読み込む。
- **各製品はルールもプラグインもコピーせず、相対パス（`../../platform/docs/design-system/…`）で参照する**（コピーすると変更が届かない）。

```jsonc
// oxlint（frontend/.oxlintrc.json）
{
  "extends": ["../../platform/docs/design-system/adherence.oxlintrc.json"]
}
```

```js
// ESLint（frontend/eslint.config.mjs）— 同じセレクタを ESLint 標準の no-restricted-syntax に渡す
import adherence from "../../platform/docs/design-system/adherence.oxlintrc.json" with { type: "json" };
const { rules } = adherence.overrides[0];
export default [
  // …
  {
    files: ["src/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-syntax": rules["design-system/restricted-syntax"],
      "no-restricted-imports": rules["no-restricted-imports"],
    },
  },
];
```

- adherence 設定は oxlint と ESLint の両方が読むため、コメントや独自キーの無い純粋な JSON に保つ（未知のキーがあると oxlint が読み込みに失敗する）。
- ルールを変えるときは、NL2SQL（oxlint）と RAG / Agent（ESLint）の `src` で違反件数を確かめて PR の `検証結果` に書く。違反が残るなら、同じ PR で製品を直すか、製品ごとの追従 Issue を作ってから merge する。
- 製品固有のルールは各製品の設定に足してよいが、同じルール名（`design-system/restricted-syntax` / `no-restricted-syntax`）は上書きしない（adherence のセレクタが消える）。
