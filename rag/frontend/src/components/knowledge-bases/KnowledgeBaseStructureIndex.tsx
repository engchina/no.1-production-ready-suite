import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FormActionBar, SaveErrorBanner, Card, CardHeader, CardTitle, CardContent, SelectField } from "@production-ready/ui";
import { Share2, Save } from "lucide-react";
import { api, type KnowledgeBaseDetail } from "@/lib/api";
import { queryKeys } from "@/lib/queries";
import { t } from "@/lib/i18n";
import { toast } from "@/lib/toast";
import { KnowledgeBaseStructureBuild } from "./KnowledgeBaseStructureBuild";

export function KnowledgeBaseStructureIndex({ kb, editable }: { kb: KnowledgeBaseDetail; editable: boolean }) {
  return <Card data-testid="knowledge-base-structure-settings">
    <CardHeader><CardTitle className="flex items-center gap-2 text-base"><Share2 className="size-4 text-fg-muted" aria-hidden />{t("knowledgeBases.structure.title")}</CardTitle></CardHeader>
    <CardContent className="space-y-4">
      <StructureSettings key={String(kb.structure_index_enabled)} kb={kb} editable={editable} />
      <KnowledgeBaseStructureBuild kbId={kb.id} enabled={kb.structure_index_enabled !== false} editable={editable} />
    </CardContent>
  </Card>;
}

function StructureSettings({ kb, editable }: { kb: KnowledgeBaseDetail; editable: boolean }) {
  const initial = kb.structure_index_enabled == null ? "inherit" : kb.structure_index_enabled ? "on" : "off";
  const [value, setValue] = useState(initial);
  const qc = useQueryClient();
  const save = useMutation({ mutationFn: () => api.updateKnowledgeBaseStructureIndex(kb.id, value === "inherit" ? null : value === "on"), onSuccess: async () => {
    await qc.invalidateQueries({ queryKey: queryKeys.knowledgeBase(kb.id) });
    toast.success(t("knowledgeBases.structure.saved"));
  } });
  return <div className="grid gap-3">
    <p className="text-sm text-fg-muted">{t("knowledgeBases.structure.help")}</p>
    <p className="text-sm text-fg-muted">{t("knowledgeBases.structure.global", { value: t(kb.structure_index_global_enabled ? "knowledgeBases.structure.on" : "knowledgeBases.structure.off") })}</p>
    <SelectField id="knowledge-base-structure-enabled" label={t("knowledgeBases.structure.title")} value={value} width="md" onValueChange={setValue}
      options={[{ value: "inherit", label: t("knowledgeBases.structure.inherit") }, { value: "on", label: t("knowledgeBases.structure.on") }, { value: "off", label: t("knowledgeBases.structure.off") }]} disabled={!editable || save.isPending} />
    <p className="text-xs text-fg-muted">{t("knowledgeBases.structure.existing")}</p>
    <SaveErrorBanner message={save.isError ? t("knowledgeBases.structure.saveError") : null} attemptKey={save.submittedAt} />
    {editable ? <FormActionBar ariaLabel={t("knowledgeBases.structure.title")} primaryActions={[{ id: "save", icon: Save, label: t("knowledgeBases.structure.save"), loading: save.isPending, disabled: initial === value, onClick: () => save.mutate() }]} /> : null}
  </div>;
}
