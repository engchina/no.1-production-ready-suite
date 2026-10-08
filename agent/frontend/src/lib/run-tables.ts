import { toTabularData, type TabularData } from "@production-ready/ui";

import type { Artifact, RunState, RunStep } from "./api";

/**
 * Run の中の表のデータ（#1158）。ツールの結果・成果物が表の形なら、共通の結果の表の部品で出す。
 * 表の形の判定は `@production-ready/ui` の `toTabularData`（製品に依存しない）。ここは Run の
 * どこから取り出すか（完了したツールの結果・成果物）だけを持つ。
 */

export interface ToolResultTable {
  stepId: string;
  toolName: string;
  data: TabularData;
}

/** ツールの結果の表（完了したツールだけ。表の形でない結果は含めない）。 */
export function stepResultTable(step: RunStep): TabularData | null {
  if (step.status !== "completed" || !step.tool_result?.success) return null;
  return toTabularData(step.tool_result.output ?? null);
}

/**
 * Run のツールの結果の表（呼んだ順）。同じジョブ（`job_id`）を何度も取り直した結果（NL2SQL の
 * `nl2sql_query` → `nl2sql_get_job`）は、最後の結果だけを残す。
 */
export function runToolResultTables(run: RunState): ToolResultTable[] {
  const tables: ToolResultTable[] = [];
  const indexByJob = new Map<string, number>();
  for (const step of run.steps) {
    const data = stepResultTable(step);
    if (!data || !step.tool_call) continue;
    const table = { stepId: step.id, toolName: step.tool_call.name, data };
    const jobId = step.tool_result?.output?.job_id;
    if (typeof jobId === "string" && jobId) {
      const previous = indexByJob.get(jobId);
      if (previous !== undefined) {
        tables[previous] = table;
        continue;
      }
      indexByJob.set(jobId, tables.length);
    }
    tables.push(table);
  }
  return tables;
}

/** 成果物の内容が表の形なら表（回答・RAG の根拠は表にしない）。 */
export function artifactTable(artifact: Artifact): TabularData | null {
  if (artifact.kind === "answer" || artifact.kind === "rag_evidence") return null;
  return toTabularData(artifact.content);
}
