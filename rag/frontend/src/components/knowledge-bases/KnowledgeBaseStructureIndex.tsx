import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { SaveErrorBanner, Card, CardHeader, CardTitle, CardContent, SelectField } from "@production-ready/ui";
import { Share2, Save } from "lucide-react";
import { api, type KnowledgeBaseDetail } from "@/lib/api";
import { queryKeys } from "@/lib/queries";
import { t } from "@/lib/i18n";
import { toast } from "@/lib/toast";
import { KnowledgeBaseStructureBuild } from "./KnowledgeBaseStructureBuild";
import { KnowledgeBaseGraphView } from "./KnowledgeBaseGraphView";

/** 文書構造索引の構築の設定・既存文書への構築・文書構造グラフを 1 枚のカードにまとめる。 */
export function KnowledgeBaseStructureIndex({ kb, editable }: { kb: KnowledgeBaseDetail; editable: boolean }) {
  const initial = kb.structure_index_enabled == null ? "inherit" : kb.structure_index_enabled ? "on" : "off";
  const [value, setValue] = useState(initial);
  // 保存や別の画面で KB の設定が変わったら選択を戻す（構築の表示の状態は保ったまま）。
  const [synced, setSynced] = useState(initial);
  if (synced !== initial) { setSynced(initial); setValue(initial); }
  const qc = useQueryClient();
  const save = useMutation({ mutationFn: () => api.updateKnowledgeBaseStructureIndex(kb.id, value === "inherit" ? null : value === "on"), onSuccess: async () => {
    await qc.invalidateQueries({ queryKey: queryKeys.knowledgeBase(kb.id) });
    toast.success(t("knowledgeBases.structure.saved"));
  } });
  return <Card data-testid="knowledge-base-structure-settings">
    <CardHeader><CardTitle className="flex items-center gap-2 text-base"><Share2 className="size-4 text-fg-muted" aria-hidden />{t("knowledgeBases.structure.title")}</CardTitle></CardHeader>
    <CardContent className="space-y-4">
      <div className="grid min-w-0 grid-cols-1 gap-3">
        <p className="text-sm text-fg-muted">{t("knowledgeBases.structure.help")}</p>
        <p className="text-sm text-fg-muted">{t("knowledgeBases.structure.global", { value: t(kb.structure_index_global_enabled ? "knowledgeBases.structure.on" : "knowledgeBases.structure.off") })}</p>
        <SelectField id="knowledge-base-structure-enabled" label={t("knowledgeBases.structure.title")} value={value} width="lg" wrapOptions onValueChange={setValue}
          options={[{ value: "inherit", label: t("knowledgeBases.structure.inherit") }, { value: "on", label: t("knowledgeBases.structure.on") }, { value: "off", label: t("knowledgeBases.structure.off") }]} disabled={!editable || save.isPending} />
        <p className="text-xs text-fg-muted">{t("knowledgeBases.structure.existing")}</p>
        <SaveErrorBanner message={save.isError ? t("knowledgeBases.structure.saveError") : null} attemptKey={save.submittedAt} />
        <KnowledgeBaseStructureBuild kbId={kb.id} enabled={kb.structure_index_enabled !== false} editable={editable}
          saveAction={{ id: "save", icon: Save, label: t("knowledgeBases.structure.save"), loading: save.isPending, disabled: initial === value, onClick: () => save.mutate() }} />
      </div>
      <KnowledgeBaseGraphView knowledgeBaseId={kb.id} />
    </CardContent>
  </Card>;
}
