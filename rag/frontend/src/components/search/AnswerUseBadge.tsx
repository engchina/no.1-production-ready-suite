import { StatusBadge } from "@production-ready/ui";
import { BookOpen } from "lucide-react";

import type { CitationAnswerUse } from "@/lib/chunk-metadata";
import { t } from "@/lib/i18n";

/**
 * 回答で引用した箇所のバッジ（#1208・#2040）。質問の条件への該当確認済みの記載は「回答で引用」、該当が確認できて
 * いない記載は「参考」。色だけで分けず、文言とアイコンで区別する。使われ方が分からない回答は「回答で引用」だけ。
 */
export function AnswerUseBadge({ use }: { use: CitationAnswerUse | null }) {
  if (use === "reference") {
    return <StatusBadge variant="neutral" icon={BookOpen} label={t("search.answerDetails.reference")} />;
  }
  return (
    <StatusBadge variant={use === "confirmed" ? "success" : "info"} label={t("search.answerDetails.modelUsed")} />
  );
}
