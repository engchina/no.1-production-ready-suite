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

export function dbAdminObjectQualifiedName(item: {
  name: string;
  owner?: string;
  qualified_name?: string;
}) {
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
  const name = formatDbAdminCatalogPart(item.name);
  return owner ? `${owner}.${name}` : name;
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
