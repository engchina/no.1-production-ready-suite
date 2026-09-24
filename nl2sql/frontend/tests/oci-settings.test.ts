import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_OCI_SETTINGS,
  validateOciSettingsDraft,
} from "../src/lib/oci-settings.ts";

test("OCI settings draft validation reports required Object Storage fields", () => {
  const errors = validateOciSettingsDraft(DEFAULT_OCI_SETTINGS);

  assert.equal(errors.userOcid, "required");
  assert.equal(errors.fingerprint, "required");
  assert.equal(errors.tenancyOcid, "required");
  assert.equal(errors.region, "required");
  assert.equal(errors.objectStorageRegion, "required");
  assert.equal(errors.objectStorageNamespace, "required");
});

test("OCI settings draft validation rejects malformed auth identifiers", () => {
  const errors = validateOciSettingsDraft({
    ...DEFAULT_OCI_SETTINGS,
    userOcid: "not-a-user-ocid",
    fingerprint: "not-a-fingerprint",
    tenancyOcid: "not-a-tenancy-ocid",
    region: "ap-osaka-1",
    objectStorageRegion: "ap-osaka-1",
    objectStorageNamespace: "exampletenancy",
  });

  assert.equal(errors.userOcid, "invalid_user_ocid");
  assert.equal(errors.fingerprint, "invalid_fingerprint");
  assert.equal(errors.tenancyOcid, "invalid_tenancy_ocid");
  assert.equal(errors.objectStorageRegion, undefined);
  assert.equal(errors.objectStorageNamespace, undefined);
});
