import { describe, expect, it } from "vitest";

import { ACCEPTED_UPLOAD_TYPES } from "./Dropzone";

describe("Dropzone accepted upload types", () => {
  const accepted = ACCEPTED_UPLOAD_TYPES.split(",");

  it("does not advertise broad image/* support beyond backend parser contracts", () => {
    expect(accepted).not.toContain("image/*");
    expect(accepted).not.toContain(".bmp");
    expect(accepted).not.toContain("image/bmp");
  });

  it("matches supported image and table entry points", () => {
    expect(accepted).toEqual(
      expect.arrayContaining([
        ".gif",
        ".webp",
        ".tif",
        ".tiff",
        ".tsv",
        "image/gif",
        "image/webp",
        "image/tif",
        "image/tiff",
        "text/tab-separated-values",
      ])
    );
  });

  it("includes upload-metadata-only formats that backend skips with explicit warnings", () => {
    expect(accepted).toEqual(
      expect.arrayContaining([
        ".msg",
        ".doc",
        ".ppt",
        ".xls",
        "application/vnd.ms-outlook",
        "application/x-msg",
        "application/msword",
        "application/vnd.ms-powerpoint",
        "application/vnd.ms-excel",
      ])
    );
  });

  it("keeps unsupported audio formats selectable so backend can return a clear skipped state", () => {
    expect(accepted).toEqual(
      expect.arrayContaining([
        ".aac",
        ".flac",
        ".m4a",
        ".mp3",
        ".ogg",
        ".wav",
        "audio/aac",
        "audio/flac",
        "audio/mp4",
        "audio/ogg",
        "audio/x-m4a",
        "application/ogg",
      ])
    );
  });

  it("keeps semantic HTML MIME variants aligned with SourceProfile", () => {
    expect(accepted).toEqual(expect.arrayContaining([".xhtml", "application/xhtml+xml"]));
  });

  it("keeps JSON Lines variants aligned with backend upload whitelist", () => {
    expect(accepted).toEqual(
      expect.arrayContaining([
        ".jsonl",
        ".ndjson",
        "application/jsonl",
        "application/jsonlines",
        "application/ndjson",
        "application/x-ndjson",
      ])
    );
  });
});
