/**
 * oxlint の JS プラグイン: デザインシステム遵守ルール。
 *
 * platform の docs/design-system/adherence.oxlintrc.json は ESLint の `no-restricted-syntax`
 * （esquery セレクタ）で書かれているが、oxlint のネイティブルールには `no-restricted-syntax` が無い。
 * 同じ `{ selector, message }` 形式のオプションをそのまま受け取るルールをここで提供し、
 * adherence のセレクタを書き換えずに `.oxlintrc.json` へ取り込めるようにする。
 */
const restrictedSyntax = {
  meta: {
    type: "problem",
    docs: { description: "ESLint の no-restricted-syntax 相当（esquery セレクタで構文を禁止する）" },
    schema: false,
  },
  create(context) {
    const visitor = {};
    for (const { selector, message } of context.options) {
      const previous = visitor[selector];
      visitor[selector] = (node) => {
        previous?.(node);
        context.report({ node, message });
      };
    }
    return visitor;
  },
};

export default {
  meta: { name: "design-system" },
  rules: { "restricted-syntax": restrictedSyntax },
};
