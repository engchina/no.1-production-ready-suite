import {
  parseAnswerPassageRecords,
  type AnswerPassageLabels,
  type AnswerPassageRecords,
} from "@production-ready/ui";

import { t } from "@/lib/i18n";

/**
 * 回答の段落の印・外した段落・回答全体の注意（#2738。契約は platform/contracts/answer-passages/passage-marks.json）。
 * 回答の記録の診断（`diagnostics.answer`。保存した回答は `answer_diagnostics`）の `passage_marks`・
 * `removed_passages`・`answer_warnings` を読む（回答の構造 `envelope` に置かれた記録も読む）。記録の無い過去の回答は
 * null（今までの表示のまま）。画面は Agent と同じ共有の `AnswerSections` で描く。
 */
export function answerPassages(diagnostics: unknown): AnswerPassageRecords | null {
  if (!diagnostics || typeof diagnostics !== "object" || Array.isArray(diagnostics)) return null;
  const raw = diagnostics as Record<string, unknown>;
  return parseAnswerPassageRecords(raw) ?? parseAnswerPassageRecords(raw.envelope);
}

/** 段落の印の文言（RAG のチャット・RAG 検索・実行履歴で同じ。Agent の同じ表示と同じ文言）。 */
export function answerPassageLabels(): AnswerPassageLabels {
  return {
    state: { unverified: t("search.answerText.passage.unverified"), warning: t("search.answerText.passage.warning") },
    stateDescription: {
      unverified: t("search.answerText.passage.unverifiedDescription"),
      warning: t("search.answerText.passage.warningDescription"),
    },
    toggleHint: t("search.answerText.passage.toggleHint"),
    removedTitle: t("search.answerText.passage.removedTitle"),
    removedNote: t("search.answerText.passage.removedNote"),
    marksTitle: t("search.answerText.passage.marksTitle"),
    marksNote: t("search.answerText.passage.marksNote"),
    jumpLabel: (quote) => t("search.answerText.passage.jump", { quote }),
    warningsTitle: t("search.answerText.passage.warningsTitle"),
  };
}


/** 保存本文の注意節を投影する。記録と完全に同じ形式の行だけ重複として除き、残りは原文の補足にする。 */
export function projectAnswerPassageSections(text: string, records: AnswerPassageRecords, format?: Readonly<Record<string, string>>) {
  const labels = answerPassageLabels();
  const bullet = format?.item_bullet ?? "・";
  const compact = (value: string) => value.replace(/\s+/gu, " ").trim();
  const short = (value: string, limit: number) => {
    const chars = Array.from(compact(value));
    return chars.length > limit ? chars.slice(0, limit).join("") + "…" : chars.join("");
  };
  const template = (value: string, args: Record<string, string>) => value.replace(/\{(\w+)\}/g, (match, key: string) => args[key] ?? match);
  const passageLine = (item: AnswerPassageRecords["removed"][number]) => {
    const body = item.text.trim();
    const quote = short((body.startsWith(bullet) ? body.slice(bullet.length) : body.replace(/^\d+\.\s/, "")), 80);
    return bullet + template(format?.passage_note_template ?? "{reasons}: 「{text}」", {
      text: quote, reasons: [...new Set(item.reasons.map(reason => reason.label || reason.code))].join("・"),
    });
  };
  const known = {
    removed: new Set(records.removed.map(passageLine)),
    marks: new Set([
      ...records.marks.map(passageLine),
      ...records.warnings.map(warning => {
        const detail = short(warning.detail, 160);
        const label = warning.label || warning.code;
        return bullet + template(format?.answer_warning_template ?? "{reasons}", { reasons: detail ? `${label}: ${detail}` : label });
      }),
    ]),
  };
  const titles = new Map<string, "removed" | "marks">([
    [format?.removed_section ?? labels.removedTitle, "removed"], [format?.marked_section ?? labels.marksTitle, "marks"],
  ]);
  const notes = new Set([
    format?.removed_section_note ?? labels.removedNote,
    format?.marked_section_note ?? labels.marksNote,
    "次の点は根拠で確かめきれていないか、注意が必要です。",
    "次の点は根拠で確かめきれていないか、注意が必要です（内容は回答に載せています）。",
    "次の内容は根拠と矛盾するため、回答から除きました。",
  ]);
  const remaining: { removed: string[]; marks: string[] } = { removed: [], marks: [] };
  const parts = text.split(/\n{2,}/);
  const kept: string[] = [];
  for (let index = 0; index < parts.length; index += 1) {
    const lines = parts[index].trim().split("\n");
    const kind = titles.get(lines[0].trim());
    if (!kind) { kept.push(parts[index]); continue; }
    let content = lines.slice(1);
    if (!content.length && index + 1 < parts.length) {
      const next = parts[index + 1].trim().split("\n");
      const first = next[0].trim();
      if (notes.has(first) || first.startsWith(bullet)) { content = next; index += 1; }
    }
    for (const raw of content) {
      const line = raw.trim();
      if (!line || notes.has(line) || known[kind].has(line)) continue;
      const value = line.startsWith(bullet) ? line.slice(bullet.length) : line;
      if (!remaining[kind].includes(value)) remaining[kind].push(value);
    }
  }
  return { body: kept.join("\n\n"), notes: remaining };
}
