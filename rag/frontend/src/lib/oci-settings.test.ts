import { describe, expect, it } from "vitest";

import {
  DEFAULT_OCI_SETTINGS,
  OCI_SETTINGS_STORAGE_KEY,
  buildOciConfigFile,
  buildOciEnvFile,
  normalizeOciSettingsDraft,
  parseOciConfigContent,
  readStoredOciSettingsDraft,
  validateOciSettingsDraft,
  type OciSettingsDraft,
} from "./oci-settings";

const COMPLETE_SETTINGS: OciSettingsDraft = {
  ...DEFAULT_OCI_SETTINGS,
  userOcid: "ocid1.user.oc1..example",
  fingerprint: "12:34:56:78:90:ab:cd:ef",
  tenancyOcid: "ocid1.tenancy.oc1..example",
  keyFile: "~/.oci/oci_api_key.pem",
  objectStorageNamespace: "mytenancy",
  objectStorageBucket: "rag-originals",
};

describe("normalizeOciSettingsDraft", () => {
  it("既定値を補完し、文字列値を trim する", () => {
    const draft = normalizeOciSettingsDraft({
      configFile: " /opt/oci/config ",
      configProfile: " PROD ",
      keyFile: " /home/app/.oci/prod.pem ",
      objectStorageNamespace: " mytenancy ",
    });

    expect(draft.configFile).toBe(DEFAULT_OCI_SETTINGS.configFile);
    expect(draft.configProfile).toBe("DEFAULT");
    expect(draft.keyFile).toBe(DEFAULT_OCI_SETTINGS.keyFile);
    expect(draft.region).toBe("us-chicago-1");
    expect(draft.objectStorageRegion).toBe("ap-osaka-1");
    expect(draft.objectStorageNamespace).toBe("mytenancy");
  });
});

describe("readStoredOciSettingsDraft", () => {
  it("保存済みの OCI 設定下書きを正規化して読む", () => {
    const storage = new Map<string, string>();
    const fakeStorage = {
      getItem: (key: string) => storage.get(key) ?? null,
      removeItem: (key: string) => {
        storage.delete(key);
      },
    };
    storage.set(
      OCI_SETTINGS_STORAGE_KEY,
      JSON.stringify({ objectStorageNamespace: " mytenancy " })
    );

    const draft = readStoredOciSettingsDraft(fakeStorage);

    expect(draft.objectStorageNamespace).toBe("mytenancy");
    expect(draft.configFile).toBe(DEFAULT_OCI_SETTINGS.configFile);
  });
});

describe("validateOciSettingsDraft", () => {
  it("必須値の欠落を検出する", () => {
    const errors = validateOciSettingsDraft(DEFAULT_OCI_SETTINGS);

    expect(errors.userOcid).toBe("required");
    expect(errors.fingerprint).toBe("required");
    expect(errors.tenancyOcid).toBe("required");
    expect(errors.objectStorageNamespace).toBe("required");
    expect(errors.objectStorageBucket).toBe("required");
  });

  it("OCI config 値と bucket 名の形式を検証する", () => {
    const errors = validateOciSettingsDraft({
      ...COMPLETE_SETTINGS,
      userOcid: "ocid1.tenancy.oc1..wrong",
      fingerprint: "not-a-fingerprint",
      tenancyOcid: "ocid1.user.oc1..wrong",
      objectStorageBucket: "bad bucket",
    });

    expect(errors.userOcid).toBe("invalid_user_ocid");
    expect(errors.fingerprint).toBe("invalid_fingerprint");
    expect(errors.tenancyOcid).toBe("invalid_tenancy_ocid");
    expect(errors.objectStorageBucket).toBe("invalid_bucket");
  });
});

describe("buildOciConfigFile", () => {
  it("OCI config ファイル内容を生成する", () => {
    const config = buildOciConfigFile({
      ...COMPLETE_SETTINGS,
      configProfile: "RAG_PROD",
      region: "ap-osaka-1",
      keyFile: "/home/app/.oci/key.pem",
    });

    expect(config).toContain("[DEFAULT]");
    expect(config).not.toContain("[RAG_PROD]");
    expect(config).toContain("user=ocid1.user.oc1..example");
    expect(config).toContain("fingerprint=12:34:56:78:90:ab:cd:ef");
    expect(config).toContain("tenancy=ocid1.tenancy.oc1..example");
    expect(config).toContain("region=ap-osaka-1");
    expect(config).toContain("key_file=~/.oci/oci_api_key.pem");
    expect(config).not.toContain("key_file=/home/app/.oci/key.pem");
    expect(config).not.toContain("compartment=");
  });
});

describe("parseOciConfigContent", () => {
  it("DEFAULT profile の OCI config 内容を draft 値へ変換する", () => {
    const parsed = parseOciConfigContent(
      `[DEFAULT]
user=ocid1.user.oc1..default
fingerprint=aa:bb:cc:dd
tenancy=ocid1.tenancy.oc1..default
region=ap-tokyo-1
key_file=~/.oci/default.pem
compartment=ocid1.compartment.oc1..default

[RAG_PROD]
user=ocid1.user.oc1..prod
fingerprint=12:34:56:78
tenancy=ocid1.tenancy.oc1..prod
region=us-chicago-1
key_file=/home/app/.oci/prod.pem
compartment=ocid1.compartment.oc1..prod`,
      "RAG_PROD"
    );

    expect(parsed.profile).toBe("DEFAULT");
    expect(parsed.values).toMatchObject({
      configProfile: "DEFAULT",
      userOcid: "ocid1.user.oc1..default",
      fingerprint: "aa:bb:cc:dd",
      tenancyOcid: "ocid1.tenancy.oc1..default",
      region: "ap-tokyo-1",
      keyFile: "~/.oci/oci_api_key.pem",
    });
    expect(parsed.appliedFields).toContain("keyFile");
    expect(parsed.appliedFields).not.toContain("compartmentId");
  });

  it("preferred profile を指定しても DEFAULT だけを読み取る", () => {
    const parsed = parseOciConfigContent(
      `[DEFAULT]
user=ocid1.user.oc1..default
fingerprint=aa:bb:cc:dd
tenancy=ocid1.tenancy.oc1..default
region=ap-tokyo-1
key_file=~/.oci/default.pem
compartment=ocid1.compartment.oc1..default

[RAG_PROD]
region=us-chicago-1`,
      "RAG_PROD"
    );

    expect(parsed.values).toMatchObject({
      configProfile: "DEFAULT",
      userOcid: "ocid1.user.oc1..default",
      fingerprint: "aa:bb:cc:dd",
      tenancyOcid: "ocid1.tenancy.oc1..default",
      region: "ap-tokyo-1",
      keyFile: "~/.oci/oci_api_key.pem",
    });
  });
});

describe("buildOciEnvFile", () => {
  it("OCI 認証に必要な環境変数を生成する", () => {
    const env = buildOciEnvFile({
      ...COMPLETE_SETTINGS,
      configFile: "/opt/oci/config",
      configProfile: "RAG_PROD",
    });

    expect(env).toContain("AI_SERVICE_ADAPTER=oci");
    expect(env).toContain("OCI_CONFIG_FILE=~/.oci/config");
    expect(env).not.toContain("OCI_CONFIG_FILE=/opt/oci/config");
    expect(env).toContain("OCI_CONFIG_PROFILE=DEFAULT");
    expect(env).not.toContain("OCI_COMPARTMENT_ID=");
    expect(env).toContain("OBJECT_STORAGE_REGION=ap-osaka-1");
    expect(env).toContain("OBJECT_STORAGE_NAMESPACE=mytenancy");
    expect(env).toContain("OBJECT_STORAGE_BUCKET=rag-originals");
  });
});
