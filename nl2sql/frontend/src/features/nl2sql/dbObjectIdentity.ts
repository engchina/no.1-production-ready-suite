export interface DbAdminObjectTarget {
  owner: string;
  name: string;
  qualifiedName: string;
}

const SIMPLE_ORACLE_IDENTIFIER = /^[A-Z][A-Z0-9_$#]{0,127}$/u;

function splitIdentifierParts(value: string): string[] {
  const parts: string[] = [];
  let buffer = "";
  let inDouble = false;
  const raw = value.trim();
  for (let index = 0; index < raw.length; index += 1) {
    const char = raw[index];
    if (char === '"') {
      buffer += char;
      if (inDouble && raw[index + 1] === '"') {
        buffer += raw[index + 1];
        index += 1;
        continue;
      }
      inDouble = !inDouble;
      continue;
    }
    if (char === "." && !inDouble) {
      const part = buffer.trim();
      if (part) parts.push(part);
      buffer = "";
      continue;
    }
    buffer += char;
  }
  if (inDouble) {
    throw new Error("Oracle 識別子が不正です。");
  }
  const tail = buffer.trim();
  if (tail) parts.push(tail);
  return parts;
}

function unquoteIdentifierPart(value: string): string {
  if (value.length < 2 || !value.startsWith('"') || !value.endsWith('"')) {
    if (value.includes('"')) throw new Error("Oracle 識別子が不正です。");
    return value;
  }
  const inner = value.slice(1, -1).replaceAll('""', "");
  if (inner.includes('"')) throw new Error("Oracle 識別子が不正です。");
  return value.slice(1, -1).replaceAll('""', '"');
}

function normalizeIdentifierPart(value: string): string {
  const trimmed = value.trim();
  if (trimmed.startsWith('"') || trimmed.endsWith('"')) return unquoteIdentifierPart(trimmed);
  const upper = trimmed.toUpperCase();
  return SIMPLE_ORACLE_IDENTIFIER.test(upper) ? upper : trimmed;
}

export function formatDbAdminObjectPart(value: string): string {
  const normalized = normalizeIdentifierPart(value);
  if (!normalized) return "";
  if (SIMPLE_ORACLE_IDENTIFIER.test(normalized)) return normalized;
  return `"${normalized.replaceAll('"', '""')}"`;
}

function formatDbAdminCatalogPart(value: string): string {
  const trimmed = value.trim();
  const normalized =
    trimmed.startsWith('"') || trimmed.endsWith('"') ? unquoteIdentifierPart(trimmed) : trimmed;
  if (!normalized) return "";
  if (SIMPLE_ORACLE_IDENTIFIER.test(normalized)) return normalized;
  return `"${normalized.replaceAll('"', '""')}"`;
}

/** 表・ビュー名の組み立てに使う入力。API の `owner` / `name` / `qualified_name` をそのまま渡す。 */
export interface DbObjectNameSource {
  name: string;
  owner?: string | null;
  qualified_name?: string | null;
}

function strictDbObjectName(item: DbObjectNameSource) {
  const qualifiedName = (item.qualified_name ?? "").trim();
  if (qualifiedName) {
    const parts = splitIdentifierParts(qualifiedName);
    if (parts.length === 2) {
      return `${formatDbAdminCatalogPart(parts[0])}.${formatDbAdminCatalogPart(parts[1])}`;
    }
    if (parts.length === 1) return formatDbAdminCatalogPart(parts[0]);
    return qualifiedName;
  }
  const owner = formatDbAdminCatalogPart(item.owner ?? "");
  const name = formatDbAdminCatalogPart(item.name ?? "");
  return owner && name ? `${owner}.${name}` : name;
}

/**
 * 表・ビュー名を `OWNER.OBJECT` 形式の文字列にする唯一の実装。
 *
 * - `qualified_name` があればそれを優先する。
 * - owner と name は Oracle のカタログ値（大文字化済み・引用符なし）として扱い、
 *   `[A-Z][A-Z0-9_$#]*` に当てはまらない部分だけを `"..."` で囲む。
 *   backend `app/features/nl2sql/object_identity.py` の `qualified_object_name` と同じ規則。
 * - owner が分からない場合は名前だけを返す（推測で補わない）。
 * - 表示用のため例外を投げない。引用符の対応が壊れた値は受け取った文字列のまま返す。
 */
export function formatDbObjectName(item: DbObjectNameSource): string {
  try {
    return strictDbObjectName(item);
  } catch {
    const qualifiedName = (item.qualified_name ?? "").trim();
    if (qualifiedName) return qualifiedName;
    const owner = (item.owner ?? "").trim();
    const name = (item.name ?? "").trim();
    return owner && name ? `${owner}.${name}` : name;
  }
}

/**
 * DeepSec のデータ権限（`target_owner` / `target_object` / `resource_code`）の表示名・比較キー。
 * backend `DataEntitlementInput` は各部の引用符を外して大文字化してから保存するため、同じ正規化をしてから
 * `formatDbObjectName` で組み立てる。大文字の単純な識別子では従来のキー（`OWNER.OBJECT` の大文字化）と一致する。
 */
export function formatEntitlementTargetName(entitlement: {
  target_owner?: string | null;
  target_object?: string | null;
  resource_code?: string | null;
}): string {
  const normalize = (value: string | null | undefined) =>
    (value ?? "").trim().replaceAll('"', "").toUpperCase();
  const owner = normalize(entitlement.target_owner);
  const object = normalize(entitlement.target_object);
  if (owner && object) return formatDbObjectName({ owner, name: object });
  const resource = (entitlement.resource_code ?? "").trim().toUpperCase();
  return resource ? formatDbObjectName({ name: "", qualified_name: resource }) : "";
}

export function parseDbAdminObjectTarget(value: string, owner = ""): DbAdminObjectTarget {
  const parts = splitIdentifierParts(value);
  const ownerPart =
    parts.length >= 2 ? formatDbAdminObjectPart(parts[0]) : formatDbAdminObjectPart(owner);
  const namePart = formatDbAdminObjectPart(parts.length >= 2 ? parts.slice(1).join(".") : value);
  return {
    owner: ownerPart,
    name: namePart,
    qualifiedName: ownerPart ? `${ownerPart}.${namePart}` : namePart,
  };
}
