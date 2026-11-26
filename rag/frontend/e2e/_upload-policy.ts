import type { Page } from "@playwright/test";

import { ACCEPTED_UPLOAD_TYPES } from "../src/lib/upload-selection";
import { apiEnvelope } from "./_helpers";

export async function mockUploadPolicy(
  page: Page,
  allowedContentTypes = [
    ...ACCEPTED_UPLOAD_TYPES.split(",").filter((type) => !type.startsWith(".")),
    "application/octet-stream",
  ],
) {
  await page.route("**/api/documents/upload-policy", (route) =>
    route.fulfill({ json: apiEnvelope({ allowed_content_types: allowedContentTypes }) }),
  );
}
