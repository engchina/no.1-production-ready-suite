/** 正式な呼出欄の全文参照。本文中のJSONは参照として解釈しない。 */
export interface CallBodyReference {
  body_id: string;
  sha256: string;
  character_count: number;
  byte_count: number;
  part_count: number;
  visibility: "owner" | "system_admin";
}
export interface CallContent {
  status: "recorded" | "truncated" | "incomplete" | "not_recorded" | "withheld";
  text: string;
  body_reference?: CallBodyReference;
  preview_only?: boolean;
  failure_code?: string;
}
export type CallBodySlot = "input" | "output" | "schema";
export interface CallBodyRequest {
  spanId: string;
  slot: CallBodySlot;
  firstPart: number;
  signal: AbortSignal;
}
/** 製品が認可付きの正本APIを選び、共通UIが応答を検証する。 */
export type CallBodyLoader = (request: CallBodyRequest) => Promise<unknown>;
export interface CallBodyPart { index: number; start: number; end: number; text: string; sha256: string }
export interface CallBodyPage {
  reference: CallBodyReference;
  status: "pending" | "complete" | "incomplete";
  failure_code: string | null;
  parts: CallBodyPart[];
  next_part: number | null;
}
function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}
const integer = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const hash = (value: unknown): value is string => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
export function parseCallBodyReference(value: unknown): CallBodyReference | undefined {
  const r = record(value);
  if (!r || typeof r.body_id !== "string" || !/^[a-f0-9]{32}$/.test(r.body_id) || !hash(r.sha256) ||
    !integer(r.character_count) || !integer(r.byte_count) || !integer(r.part_count) || r.part_count < 1 ||
    (r.visibility !== "owner" && r.visibility !== "system_admin")) return undefined;
  return {body_id: r.body_id, sha256: r.sha256, character_count: r.character_count, byte_count: r.byte_count, part_count: r.part_count, visibility: r.visibility};
}
export function parseCallContent(value: unknown): CallContent {
  const item = record(value);
  const status = item?.status;
  if (!item || (status !== "recorded" && status !== "truncated" && status !== "incomplete")) {
    return {status: status === "withheld" ? "withheld" : "not_recorded", text: ""};
  }
  const reference = parseCallBodyReference(item.body_reference);
  return {status, text: typeof item.text === "string" ? item.text : "",
    ...(reference ? {body_reference: reference} : {}),
    ...(item.preview_only === true ? {preview_only: true} : {}),
    ...(typeof item.failure_code === "string" ? {failure_code: item.failure_code} : {})};
}
export function parseCallBodyPage(value: unknown, expected: CallBodyReference, firstPart: number): CallBodyPage | null {
  const item = record(value), reference = parseCallBodyReference(item?.reference);
  if (!item || !reference || Object.keys(expected).some(key => expected[key as keyof CallBodyReference] !== reference[key as keyof CallBodyReference]) ||
    !["pending", "complete", "incomplete"].includes(String(item.status)) || !Array.isArray(item.parts) ||
    (item.next_part !== null && (!integer(item.next_part) || item.next_part <= firstPart || item.next_part >= reference.part_count))) return null;
  const parts: CallBodyPart[] = [];
  for (const value of item.parts) {
    const part = record(value), index = firstPart + parts.length;
    if (!part || part.index !== index || index >= reference.part_count || !integer(part.start) || !integer(part.end) ||
      part.end < part.start || part.end > reference.character_count || typeof part.text !== "string" ||
      [...part.text].length !== part.end - part.start || !hash(part.sha256) ||
      (parts.length && parts[parts.length - 1].end !== part.start) || (firstPart === 0 && parts.length === 0 && part.start !== 0)) return null;
    parts.push({index, start: part.start, end: part.end, text: part.text, sha256: part.sha256});
  }
  if (item.status === "complete" && (!parts.length ||
    (item.next_part === null ? parts.at(-1)!.end !== reference.character_count || firstPart + parts.length !== reference.part_count : item.next_part !== firstPart + parts.length))) return null;
  return {reference, status: item.status as CallBodyPage["status"], failure_code: typeof item.failure_code === "string" ? item.failure_code : null, parts, next_part: item.next_part as number | null};
}

export function callBodyPath(tracePath: string, request: Pick<CallBodyRequest, "spanId" | "slot" | "firstPart">): string {
  return `${tracePath}/spans/${encodeURIComponent(request.spanId)}/${request.slot}/body?${new URLSearchParams({first_part: String(request.firstPart)})}`;
}
