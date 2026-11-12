import { describe, expect, it } from "vitest";

import { ACCEPTED_UPLOAD_TYPES } from "@/lib/upload-selection";

describe("Dropzone accepted upload types", () => {
  const accepted = ACCEPTED_UPLOAD_TYPES.split(",");

  it("does not advertise broad image/* support beyond backend parser contracts", () => {
    expect(accepted).not.toContain("image/*");
    expect(accepted).not.toContain(".bmp");
    expect(accepted).not.toContain("image/bmp");
  });

  // 主要な形式だけを受ける（#4730）。
  it("accepts only the main formats", () => {
    expect(accepted.filter((item) => item.startsWith(".")).sort()).toEqual(
      [
        ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".csv", ".txt",
        ".md", ".markdown", ".json", ".html", ".htm", ".jpg", ".jpeg", ".png",
      ].sort()
    );
  });

  // 音声・動画は取り込まない形式（#3369）。
  it("does not offer audio or video files, which are not ingested", () => {
    for (const item of [".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav", ".mp4", ".mov", ".webm"]) {
      expect(accepted).not.toContain(item);
    }
    expect(accepted.filter((item) => item.startsWith("audio/") || item.startsWith("video/"))).toEqual([]);
    expect(accepted).not.toContain("application/ogg");
  });

  it("hides formats whose parsers are not installed", () => {
    // pandoc・pi-heif を入れないため（#3351）。
    for (const item of [".heic", ".heif", ".epub", "image/heic", "image/heif", "application/epub+zip"]) {
      expect(accepted).not.toContain(item);
    }
  });
});
