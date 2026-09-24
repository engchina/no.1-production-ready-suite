export const OCI_SETTINGS_STORAGE_KEY = "production-ready-nl2sql.oci-settings.v1";
export const FIXED_OCI_CONFIG_FILE = "~/.oci/config";
export const FIXED_OCI_CONFIG_PROFILE = "DEFAULT";
export const FIXED_OCI_KEY_FILE = "~/.oci/oci_api_key.pem";

export interface OciSettingsDraft {
  configFile: string;
  configProfile: string;
  userOcid: string;
  fingerprint: string;
  tenancyOcid: string;
  keyFile: string;
  region: string;
  objectStorageRegion: string;
  objectStorageNamespace: string;
}

export type OciSettingsField = keyof OciSettingsDraft;

export type OciValidationCode =
  | "required"
  | "invalid_fingerprint"
  | "invalid_tenancy_ocid"
  | "invalid_user_ocid"
  | "invalid_profile";

export type OciValidationResult = Partial<Record<OciSettingsField, OciValidationCode>>;

export const DEFAULT_OCI_SETTINGS: OciSettingsDraft = {
  configFile: FIXED_OCI_CONFIG_FILE,
  configProfile: FIXED_OCI_CONFIG_PROFILE,
  userOcid: "",
  fingerprint: "",
  tenancyOcid: "",
  keyFile: FIXED_OCI_KEY_FILE,
  region: "",
  objectStorageRegion: "",
  objectStorageNamespace: "",
};

export const REQUIRED_OCI_SETTINGS_FIELDS = [
  "configFile",
  "configProfile",
  "userOcid",
  "fingerprint",
  "tenancyOcid",
  "keyFile",
  "region",
  "objectStorageRegion",
  "objectStorageNamespace",
] as const satisfies readonly OciSettingsField[];

const OCI_FINGERPRINT_PATTERN = /^[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2})+$/;
const OCI_CONFIG_KEYS = [
  "user",
  "fingerprint",
  "tenancy",
  "region",
  "key_file",
] as const;

type OciConfigKey = (typeof OCI_CONFIG_KEYS)[number];

interface OciConfigSection {
  name: string;
  entries: Partial<Record<OciConfigKey, string>>;
}

export interface OciConfigParseResult {
  profile: string;
  values: Partial<OciSettingsDraft>;
  appliedFields: OciSettingsField[];
}

export function normalizeOciSettingsDraft(input: Partial<OciSettingsDraft>): OciSettingsDraft {
  return {
    configFile: FIXED_OCI_CONFIG_FILE,
    configProfile: FIXED_OCI_CONFIG_PROFILE,
    userOcid: (input.userOcid ?? DEFAULT_OCI_SETTINGS.userOcid).trim(),
    fingerprint: (input.fingerprint ?? DEFAULT_OCI_SETTINGS.fingerprint).trim(),
    tenancyOcid: (input.tenancyOcid ?? DEFAULT_OCI_SETTINGS.tenancyOcid).trim(),
    keyFile: FIXED_OCI_KEY_FILE,
    region: (input.region ?? DEFAULT_OCI_SETTINGS.region).trim(),
    objectStorageRegion: (
      input.objectStorageRegion ?? DEFAULT_OCI_SETTINGS.objectStorageRegion
    ).trim(),
    objectStorageNamespace: (
      input.objectStorageNamespace ?? DEFAULT_OCI_SETTINGS.objectStorageNamespace
    ).trim(),
  };
}

export function readStoredOciSettingsDraft(
  storage: Pick<Storage, "getItem" | "removeItem"> | null =
    typeof window === "undefined" ? null : window.localStorage
): OciSettingsDraft {
  if (!storage) return DEFAULT_OCI_SETTINGS;
  try {
    const stored = storage.getItem(OCI_SETTINGS_STORAGE_KEY);
    if (!stored) return DEFAULT_OCI_SETTINGS;
    const parsed = normalizeOciSettingsDraft(JSON.parse(stored) as Partial<OciSettingsDraft>);
    return normalizeOciSettingsDraft({
      objectStorageRegion: parsed.objectStorageRegion,
      objectStorageNamespace: parsed.objectStorageNamespace,
    });
  } catch {
    storage.removeItem(OCI_SETTINGS_STORAGE_KEY);
    return DEFAULT_OCI_SETTINGS;
  }
}

export function validateOciSettingsDraft(draft: OciSettingsDraft): OciValidationResult {
  const normalized = normalizeOciSettingsDraft(draft);
  const errors: OciValidationResult = {};

  for (const field of REQUIRED_OCI_SETTINGS_FIELDS) {
    if (!normalized[field]) {
      errors[field] = "required";
    }
  }

  if (normalized.userOcid && !normalized.userOcid.startsWith("ocid1.user.")) {
    errors.userOcid = "invalid_user_ocid";
  }

  if (normalized.tenancyOcid && !normalized.tenancyOcid.startsWith("ocid1.tenancy.")) {
    errors.tenancyOcid = "invalid_tenancy_ocid";
  }

  if (
    normalized.fingerprint &&
    !OCI_FINGERPRINT_PATTERN.test(normalized.fingerprint)
  ) {
    errors.fingerprint = "invalid_fingerprint";
  }

  return errors;
}

export function buildOciEnvFile(draft: OciSettingsDraft): string {
  const normalized = normalizeOciSettingsDraft(draft);

  const groups: [string, [string, string][]][] = [
    [
      "OCI 共通",
      [
        ["OCI_CONFIG_FILE", normalized.configFile],
        ["OCI_CONFIG_PROFILE", normalized.configProfile],
        ...envEntry("OCI_REGION", normalized.region),
      ],
    ],
    [
      "OCI Object Storage",
      [
        ...envEntry("OBJECT_STORAGE_REGION", normalized.objectStorageRegion),
        ...envEntry("OBJECT_STORAGE_NAMESPACE", normalized.objectStorageNamespace),
      ],
    ],
  ];

  return groups
    .map(([title, entries]) => {
      const lines = entries.map(([key, value]) => `${key}=${formatEnvValue(value)}`);
      return [`# ${title}`, ...lines].join("\n");
    })
    .join("\n\n");
}

export function buildOciConfigFile(draft: OciSettingsDraft): string {
  const normalized = normalizeOciSettingsDraft(draft);
  const authLines = [
    ...configLine("user", normalized.userOcid),
    ...configLine("fingerprint", normalized.fingerprint),
    ...configLine("tenancy", normalized.tenancyOcid),
    ...configLine("region", normalized.region),
  ];

  return [
    `[${FIXED_OCI_CONFIG_PROFILE}]`,
    ...authLines,
    ...configLine("key_file", authLines.length > 0 ? normalized.keyFile : ""),
  ].join("\n");
}

export function parseOciConfigContent(
  content: string,
  _preferredProfile = FIXED_OCI_CONFIG_PROFILE
): OciConfigParseResult {
  const sections = parseConfigSections(content);
  const selected = sections.find(
    (section) => section.name.toUpperCase() === FIXED_OCI_CONFIG_PROFILE
  );

  if (!selected) {
    return { profile: FIXED_OCI_CONFIG_PROFILE, values: {}, appliedFields: [] };
  }

  const values: Partial<OciSettingsDraft> = { configProfile: FIXED_OCI_CONFIG_PROFILE };
  const appliedFields: OciSettingsField[] = ["configProfile"];
  const selectedEntries = selected.entries;
  const fieldMap: Record<OciConfigKey, OciSettingsField> = {
    user: "userOcid",
    fingerprint: "fingerprint",
    tenancy: "tenancyOcid",
    region: "region",
    key_file: "keyFile",
  };

  for (const key of OCI_CONFIG_KEYS) {
    const value = selectedEntries[key]?.trim();
    if (!value) continue;
    const field = fieldMap[key];
    values[field] = (field === "keyFile" ? FIXED_OCI_KEY_FILE : value) as never;
    appliedFields.push(field);
  }

  return { profile: FIXED_OCI_CONFIG_PROFILE, values, appliedFields };
}

function parseConfigSections(content: string): OciConfigSection[] {
  const defaultSection: OciConfigSection = { name: "DEFAULT", entries: {} };
  const sections = new Map<string, OciConfigSection>([["DEFAULT", defaultSection]]);
  let current = defaultSection;

  for (const rawLine of content.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#") || line.startsWith(";")) continue;

    const sectionMatch = /^\[([^\]]+)\]$/.exec(line);
    if (sectionMatch) {
      const sectionName = sectionMatch[1].trim();
      if (!sections.has(sectionName)) {
        sections.set(sectionName, { name: sectionName, entries: {} });
      }
      current = sections.get(sectionName) ?? defaultSection;
      continue;
    }

    const separatorIndex = line.indexOf("=");
    if (separatorIndex < 1) continue;

    const key = line.slice(0, separatorIndex).trim();
    if (!isOciConfigKey(key)) continue;
    current.entries[key] = line.slice(separatorIndex + 1).trim();
  }

  return [...sections.values()].filter((section) =>
    OCI_CONFIG_KEYS.some((key) => section.entries[key])
  );
}

function isOciConfigKey(key: string): key is OciConfigKey {
  return (OCI_CONFIG_KEYS as readonly string[]).includes(key);
}

function formatEnvValue(value: string): string {
  if (!value) return "";
  if (/^[A-Za-z0-9_./:@~+=,-]+$/.test(value)) return value;
  return JSON.stringify(value);
}

function envEntry(key: string, value: string): [string, string][] {
  return value.trim() ? [[key, value]] : [];
}

function configLine(key: string, value: string): string[] {
  return value.trim() ? [`${key}=${value}`] : [];
}
