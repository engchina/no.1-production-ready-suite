import { Card, CardHeader, CardTitle, CardContent } from "@production-ready/ui";
import { Share2 } from "lucide-react";
import type { KnowledgeBaseDetail } from "@/lib/api";
import { t } from "@/lib/i18n";
import { KnowledgeBaseStructureBuild } from "./KnowledgeBaseStructureBuild";
import { KnowledgeBaseGraphView } from "./KnowledgeBaseGraphView";

/** 文書構造索引の手動の構築と文書構造グラフを 1 枚のカードにまとめる。取込では作らない（#5484）。 */
export function KnowledgeBaseStructureIndex({ kb, editable }: { kb: KnowledgeBaseDetail; editable: boolean }) {
  return <Card data-testid="knowledge-base-structure-settings">
    <CardHeader><CardTitle className="flex items-center gap-2 text-base"><Share2 className="size-4 text-fg-muted" aria-hidden />{t("knowledgeBases.structure.title")}</CardTitle></CardHeader>
    <CardContent className="space-y-4">
      <div className="grid min-w-0 grid-cols-1 gap-3">
        <p className="text-sm text-fg-muted">{t("knowledgeBases.structure.help")}</p>
        <KnowledgeBaseStructureBuild kbId={kb.id} editable={editable} />
      </div>
      <KnowledgeBaseGraphView knowledgeBaseId={kb.id} />
    </CardContent>
  </Card>;
}
