import { CircleArrowRight, CircleCheckBig, CircleSlash, Link2, ListChecks, TriangleAlert, type LucideIcon } from "lucide-react";

import type { WikiGraphRelation } from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";

/** 関係の種類ごとのアイコン。色だけにせず、形と文字で区別する（#5502）。 */
export const WIKI_RELATION_ICONS: Record<WikiGraphRelation, LucideIcon> = {
  exception: CircleSlash,
  prerequisite: ListChecks,
  warning: TriangleAlert,
  continuation: CircleArrowRight,
  expected_result: CircleCheckBig,
  reference: Link2,
};

export function wikiRelationText(relation: WikiGraphRelation): string {
  return t(`wiki.graph.relation.${relation}` as I18nKey);
}

/** 関係の種類のアイコンと文字（凡例・関係の一覧で同じ表示）。 */
export function WikiRelationLabel({ relation }: { relation: WikiGraphRelation }) {
  const Icon = WIKI_RELATION_ICONS[relation];
  return <span className="inline-flex items-center gap-1.5" data-relation={relation}>
    <Icon className="size-4 shrink-0 text-fg-muted" aria-hidden />
    {wikiRelationText(relation)}
  </span>;
}
