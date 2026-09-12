import type { ReactNode } from "react";
import { t } from "@/lib/i18n";
import type { OntologyBuildJob } from "./types";
import type { WorkflowProgressStepStatus } from "../components/WorkflowProgressStrip";

import { conceptKinds } from "./conceptModel";
export { conceptKinds, conceptGraph, nodeConceptKind } from "./conceptModel";
export type { ConceptKind } from "./conceptModel";

export function conceptLabel(kind: string) { return t(`ontologyResults.kind.${kind}`); }

interface Progress {id:string;label:string;status:WorkflowProgressStepStatus;statusLabel:string;description?:string;content?:ReactNode;testId?:string;dataStatus?:string;elapsedLabel?:string;open?:boolean;}
export function orderedBuildProgress(job:OntologyBuildJob, steps:Progress[]):Progress[] {
  if (!job.definition_phases?.length) return steps; // 旧タスクの履歴は書き換えない。
  const phase = (names:string[]):Progress[] => (job.definition_phases ?? []).filter(p=>names.includes(p.name)).map(p=>({id:p.name,label:t(`ontologyResults.phase.${p.name}`),status:(p.status === "succeeded" ? "done" : p.status === "failed" ? "error" : p.status) as WorkflowProgressStepStatus,statusLabel:t(`ontologyResults.phaseStatus.${p.status}`),description:p.detail_ja}));
  const take=(names:string[])=>steps.filter(s=>names.includes(s.id));
  const groups:[string,string,Progress[]][] = [
    ["prepare","markdownOntology.progress.prepare",take(["source_extraction","schema_context"])],
    ["objects","markdownOntology.progress.objects",take(["schema_naming","qa_extraction","text_extraction"])],
    ["supplement","markdownOntology.progress.supplement",phase(["shared","capabilities"])],
    ["validation","ontologyResults.phase.validation",phase(["validation"])],
    ["markdown","ontologyResults.phase.markdown",phase(["markdown"])],
    ["save","ontologyResults.phase.save",phase(["save"])],
  ];
  // 完了した旧六段階 job は registration 内の保存経過を独立した既存履歴として保持。
  if (!job.definition_phases.some(p=>p.name === "markdown")) groups[4][2] = take(["proposal_registration"]);
  else groups[5][2].push(...take(["proposal_registration"]));
  return groups.filter(([, ,children])=>children.length).map(([id,label,children])=>{
    const statuses=children.map(s=>s.status);
    const status = statuses.includes("error") ? "error" : statuses.includes("running") ? "running" : statuses.every(s=>s==="done"||s==="skipped") ? "done" : "pending";
    return {id,testId:`ontology-build-stage-${id}`,dataStatus:status === "done" ? "succeeded" : status === "error" ? "failed" : status,label:t(label),status:status as WorkflowProgressStepStatus,statusLabel:t(`profiles.ontologyBuild.stepStatus.${status === "done" ? "succeeded" : status === "error" ? "failed" : status}`),open:status === "running" || status === "error",content:<div className="grid gap-3">{id === "supplement" && <div className="grid gap-2 text-sm">{[conceptKinds.slice(0,6),conceptKinds.slice(6)].map((kinds,i)=><details key={i}><summary>{t(i?"markdownOntology.auxConcepts":"markdownOntology.mainConcepts")}</summary><ul className="grid gap-1 pl-4">{kinds.map(k=><li key={k}>{conceptLabel(k)} · {job.concept_coverage?.find(c=>c.kind===k)?.count ? t("ontologyResults.phaseStatus.succeeded") : job.concept_coverage?.find(c=>c.kind===k)?.reason_ja || t("ontologyResults.phaseStatus.pending")}</li>)}</ul></details>)}</div>}{children.map(s=><details key={s.id} open={s.open} data-testid={s.testId} data-step-status={s.dataStatus} className="min-w-0"><summary className="cursor-pointer text-sm">{s.label} · {s.statusLabel}{s.elapsedLabel ? ` · ${s.elapsedLabel}` : ""}</summary><p className="text-sm text-muted">{s.description}</p>{s.content}</details>)}</div>};
  });
}
