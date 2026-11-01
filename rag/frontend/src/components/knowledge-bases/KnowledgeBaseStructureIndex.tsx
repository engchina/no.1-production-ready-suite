import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, Card, CardHeader, CardTitle, CardContent, SelectField } from "@production-ready/ui";
import { Share2, Save } from "lucide-react";
import { ApiErrorState } from "@/components/StateViews";
import { api, type KnowledgeBaseDetail } from "@/lib/api";
import { queryKeys } from "@/lib/queries";
import { t } from "@/lib/i18n";
import { toast } from "@/lib/toast";

export function KnowledgeBaseStructureIndex({kb, editable}:{kb:KnowledgeBaseDetail;editable:boolean}) {
  const initial=kb.structure_index_enabled == null ? "inherit" : kb.structure_index_enabled ? "on" : "off";
  const [value,setValue]=useState(initial);
  const qc=useQueryClient();
  const save=useMutation({mutationFn:()=>api.updateKnowledgeBaseStructureIndex(kb.id,value==="inherit"?null:value==="on"),onSuccess:()=>{
    toast.success(t("knowledgeBases.structure.saved"));
    void qc.invalidateQueries({queryKey:queryKeys.knowledgeBase(kb.id)});
  }});
  return <Card data-testid="knowledge-base-structure-settings">
    <CardHeader><CardTitle className="flex items-center gap-2 text-base"><Share2 className="size-4 text-fg-muted" aria-hidden />{t("knowledgeBases.structure.title")}</CardTitle></CardHeader>
    <CardContent className="space-y-4">
      <p className="text-sm text-fg-muted">{t("knowledgeBases.structure.help")}</p>
      <p className="text-sm text-fg-muted">{t("knowledgeBases.structure.global",{value:t(kb.structure_index_global_enabled?"knowledgeBases.structure.on":"knowledgeBases.structure.off")})}</p>
      <SelectField id="knowledge-base-structure-enabled" label={t("knowledgeBases.structure.title")} value={value} onValueChange={setValue}
        options={[{value:"inherit",label:t("knowledgeBases.structure.inherit")},{value:"on",label:t("knowledgeBases.structure.on")},{value:"off",label:t("knowledgeBases.structure.off")}]} disabled={!editable||save.isPending}/>
      <p className="text-xs text-fg-muted">{t("knowledgeBases.structure.existing")}</p>
      {editable?<Button icon={Save} variant="secondary" size="sm" loading={save.isPending} disabled={initial===value} onClick={()=>save.mutate()}>{t("knowledgeBases.structure.save")}</Button>:null}
      {save.isError?<ApiErrorState error={save.error} fallback={t("knowledgeBases.structure.saveError")} onRetry={()=>save.mutate()}/>:null}
    </CardContent>
  </Card>;
}
