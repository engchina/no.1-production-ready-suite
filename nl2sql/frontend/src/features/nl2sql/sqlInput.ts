// 利用者の入力が SQL の文そのものかの判定（#1715）。backend の `app/features/nl2sql/sql_input.py` と同じ規則。
//
// AI要件確認（#1439 / #1442）は自然言語の質問から SQL の要件を確かめる。入力が SQL の文そのもの
// （`select * from employee` など）なら確かめることが無いので、LLM を呼ばずに SQL の生成へ進む。
//
// - SELECT か WITH で始まる 1 文。前後の空白・コメント（`--` と `/* */`）・末尾の `;` は許す。
// - 文字列（'…'）と引用符付きの名前（"…"）の外に、もう 1 つの `;`（2 つ目の文）が無い。
// - 文字列と引用符付きの名前の外は ASCII だけ（SQL の語で始まる日本語の質問は SQL の文として扱わない）。
// - SELECT は FROM を持つ。WITH は `<名前> AS (` と SELECT を持つ。
//
// node:test(jiti)から直接 import されるため、"@/" alias を使わない。

const SELECT_RE = /^select\b/i;
const WITH_RE = /^with\s+(?:recursive\s+)?[A-Za-z_][\w$#]*(?:\s*\([^()]*\))?\s+as\s*\(/i;
const FROM_RE = /\bfrom\b/i;
const SELECT_WORD_RE = /\bselect\b/i;
// ASCII の外の文字（サロゲートを含む U+0080 以降）。
const NON_ASCII_RE = /[\u0080-\uffff]/;

/** コメントを除き、文字列・引用符付きの名前を空にした SQL の骨組み（閉じていなければ null）。 */
function codeOutsideLiterals(text: string): string | null {
  let out = "";
  let index = 0;
  while (index < text.length) {
    const char = text[index];
    const pair = text.slice(index, index + 2);
    if (pair === "--") {
      const end = text.indexOf("\n", index);
      index = end === -1 ? text.length : end;
      out += " ";
      continue;
    }
    if (pair === "/*") {
      const end = text.indexOf("*/", index + 2);
      if (end === -1) return null;
      index = end + 2;
      out += " ";
      continue;
    }
    if (char === "'" || char === '"') {
      const quote = char;
      index += 1;
      for (;;) {
        const end = text.indexOf(quote, index);
        if (end === -1) return null;
        // '' / "" は引用符そのもの（閉じていない）。
        if (text[end + 1] === quote) {
          index = end + 2;
          continue;
        }
        index = end + 1;
        break;
      }
      out += quote + quote;
      continue;
    }
    out += char;
    index += 1;
  }
  return out;
}

/** 入力が SQL の文（SELECT / WITH で始まる 1 文）か。 */
export function isSqlStatementInput(text: string): boolean {
  const raw = codeOutsideLiterals(text ?? "");
  if (raw === null) return false;
  let code = raw.trim();
  while (code.endsWith(";")) code = code.slice(0, -1).trimEnd();
  if (!code || code.includes(";") || NON_ASCII_RE.test(code)) return false;
  if (SELECT_RE.test(code)) return FROM_RE.test(code);
  if (WITH_RE.test(code)) return SELECT_WORD_RE.test(code);
  return false;
}
