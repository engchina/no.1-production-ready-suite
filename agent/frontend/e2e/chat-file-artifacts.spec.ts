import type { Page } from "@playwright/test";

import { MOCK_NOW, expect, test, type MockApi } from "./fixtures/mock-api";
import { expectNoPageHorizontalOverflow } from "./fixtures/page-overflow";

// #5837: 業務 Agent のツールが作ったファイル（成果物 `file`）を、チャットの回答の下と実行の詳細の「成果物」から
// ダウンロードする。成果物はファイル名・形式・大きさだけを持ち、中身はダウンロードの API から取る。
// Markdown（.md。#5867）も同じ行で、プレビューはしない。

const THREAD_ID = `thread_${"c".repeat(32)}`;
const RUN_ID = "run-file-artifacts";
const XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation";
const MARKDOWN = "text/markdown; charset=utf-8";
// 合成の中身（zip の署名で始まる）。
const FILE_BODY = Buffer.from("PK\u0003\u0004synthetic-file", "binary");
const LONG_NAME = `合成の長い名前のファイル_${"synthetic_name_".repeat(12)}.pptx`;
const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile-375", width: 375, height: 812 },
];

function fileArtifact(id: string, fileName: string, mimeType: string, sizeBytes: number) {
  return {
    id,
    name: fileName,
    kind: "file",
    content: { file_name: fileName, mime_type: mimeType, size_bytes: sizeBytes, sha256: "0".repeat(64) },
    created_at: MOCK_NOW,
  };
}

function seedRun(mockApi: MockApi) {
  mockApi.state.runs.push({
    id: RUN_ID,
    goal: "合成の表を作って",
    agent_id: "default",
    runtime_id: "builtin",
    status: "completed",
    steps: [],
    events: [],
    approvals: [],
    artifacts: [
      fileArtifact("artifact-xlsx", "合成の集計.xlsx", XLSX, 2048),
      fileArtifact("artifact-pptx", LONG_NAME, PPTX, 3 * 1024 * 1024),
      fileArtifact("artifact-md", "合成の手順.md", MARKDOWN, 512),
      { id: "answer", name: "回答", kind: "answer", content: { text: "合成のファイルを作りました。" }, created_at: MOCK_NOW },
    ],
    pending_tool_calls: [],
    metadata: {},
    created_by_user_uuid: "local",
    thread_id: THREAD_ID,
    created_at: MOCK_NOW,
    updated_at: MOCK_NOW,
  });
}

async function openThread(page: Page) {
  const viewport = page.viewportSize();
  await page.getByTestId("chat-history-toggle").click();
  if (viewport && viewport.width >= 1024) {
    await page.getByTestId("chat-history").getByRole("button", { name: /^合成の表を作って/ }).click();
  } else {
    await page.getByRole("dialog", { name: "会話の履歴" }).getByText("合成の表を作って").click();
  }
}

/** ダウンロードの API をモックし、要求された path を集める。 */
async function mockDownload(page: Page): Promise<string[]> {
  const requested: string[] = [];
  await page.route("**/api/runs/*/artifacts/*/download", async (route) => {
    requested.push(new URL(route.request().url()).pathname);
    await route.fulfill({
      status: 200,
      contentType: XLSX,
      headers: {
        "Content-Disposition": "attachment; filename=\"download.xlsx\"",
        "X-Content-Type-Options": "nosniff",
      },
      body: FILE_BODY,
    });
  });
  return requested;
}

for (const viewport of VIEWPORTS) {
  for (const theme of ["light", "dark"] as const) {
    test(
      `チャットの回答の下に、作成したファイルごとのダウンロードを出す (${viewport.name}, ${theme})`,
      async ({ page, mockApi }, testInfo) => {
        seedRun(mockApi);
        const requested = await mockDownload(page);
        await page.setViewportSize({ width: viewport.width, height: viewport.height });
        await page.addInitScript((value) => {
          window.localStorage.setItem("production-ready-agent.ui", JSON.stringify({ state: { theme: value }, version: 0 }));
        }, theme);
        await page.goto("/chat");
        await openThread(page);

        const files = page.getByTestId(`chat-turn-${RUN_ID}`).getByRole("region", { name: "作成したファイル" });
        await expect(files).toBeVisible();
        // 1 件 = ファイル名・形式・大きさ・ダウンロードのボタン。
        const xlsx = files.getByTestId(`chat-files-${RUN_ID}-artifact-xlsx`);
        await expect(xlsx).toContainText("合成の集計.xlsx");
        await expect(xlsx).toContainText("Excel（.xlsx）・2 KB");
        const pptx = files.getByTestId(`chat-files-${RUN_ID}-artifact-pptx`);
        await expect(pptx).toContainText(LONG_NAME);
        await expect(pptx).toContainText("PowerPoint（.pptx）・3 MB");
        const md = files.getByTestId(`chat-files-${RUN_ID}-artifact-md`);
        await expect(md).toContainText("合成の手順.md");
        await expect(md).toContainText("Markdown（.md）・512 B");
        // 長い名前は折り返し、行と画面を横にあふれさせない。
        await expectNoPageHorizontalOverflow(page);
        for (const row of [xlsx, pptx, md]) {
          const [rowBox, buttonBox] = await Promise.all([
            row.boundingBox(),
            row.getByRole("button", { name: /をダウンロード$/ }).boundingBox(),
          ]);
          expect(buttonBox!.x + buttonBox!.width).toBeLessThanOrEqual(rowBox!.x + rowBox!.width + 1);
        }
        await files.screenshot({ path: testInfo.outputPath(`chat-files-${viewport.name}-${theme}.png`) });

        // ボタンの名前はファイル名を含む（同じ文言のボタンを区別できる）。押すと、その成果物のダウンロードを要求し、
        // 成果物のファイル名で保存する。
        const downloading = page.waitForEvent("download");
        await xlsx.getByRole("button", { name: "合成の集計.xlsx をダウンロード" }).click();
        const download = await downloading;
        expect(download.suggestedFilename()).toBe("合成の集計.xlsx");
        expect(requested).toEqual([`/api/runs/${RUN_ID}/artifacts/artifact-xlsx/download`]);
        const saved = testInfo.outputPath("downloaded.xlsx");
        await download.saveAs(saved);
        const { readFile } = await import("node:fs/promises");
        expect(await readFile(saved)).toEqual(FILE_BODY);
      }
    );
  }
}

test("キーボードでダウンロードでき、失敗はその行の直下に出す（権限なしは権限なしの画面）", async ({ page, mockApi }) => {
  seedRun(mockApi);
  let status = 404;
  await page.route("**/api/runs/*/artifacts/*/download", (route) =>
    route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify({
        data: null,
        error_messages: [status === 404 ? "成果物が見つかりません。" : "この操作を行う権限がありません。"],
        warning_messages: [],
      }),
    })
  );
  await page.goto("/chat");
  await openThread(page);

  const xlsx = page.getByTestId(`chat-files-${RUN_ID}-artifact-xlsx`);
  const button = xlsx.getByRole("button", { name: "合成の集計.xlsx をダウンロード" });
  await button.focus();
  await page.keyboard.press("Enter");
  const error = page.getByTestId(`chat-files-${RUN_ID}-artifact-xlsx-error`);
  await expect(error).toContainText("成果物が見つかりません。");
  // 失敗の後もボタンは押せる（押し直せる）。ほかのファイルの行には出さない。
  await expect(button).toBeEnabled();
  await expect(page.getByTestId(`chat-files-${RUN_ID}-artifact-pptx-error`)).toHaveCount(0);
  // 失敗の案内は、そのファイルの行の直下。
  const [rowBox, errorBox] = await Promise.all([button.boundingBox(), error.boundingBox()]);
  expect(errorBox!.y).toBeGreaterThan(rowBox!.y);

  // 経路の権限拒否（403）は、ほかの API と同じく権限なしの画面へ移す（#224）。
  status = 403;
  await button.click();
  await expect(page.getByRole("heading", { name: "この機能を利用する権限がありません" })).toBeVisible();
});

test("実行の詳細の「成果物」にも、ファイルのダウンロードを出す", async ({ page, mockApi }) => {
  seedRun(mockApi);
  const requested = await mockDownload(page);
  await page.goto(`/runs?id=${RUN_ID}`);

  const section = page.getByTestId("run-artifacts-section");
  const pptx = section.getByTestId("run-artifact-artifact-pptx");
  await expect(pptx).toContainText(LONG_NAME);
  await expect(pptx).toContainText("PowerPoint（.pptx）・3 MB");
  // 中身の無い成果物の JSON（sha256 など）は出さない。
  await expect(section).not.toContainText("sha256");
  const downloading = page.waitForEvent("download");
  await pptx.getByRole("button", { name: `${LONG_NAME} をダウンロード` }).click();
  expect((await downloading).suggestedFilename()).toBe(LONG_NAME);
  expect(requested).toEqual([`/api/runs/${RUN_ID}/artifacts/artifact-pptx/download`]);
  await expectNoPageHorizontalOverflow(page);
});

test("ファイルの無い実行には、作成したファイルの節を出さない", async ({ page, mockApi }) => {
  seedRun(mockApi);
  const run = mockApi.state.runs[0] as { artifacts: Record<string, unknown>[] };
  run.artifacts = run.artifacts.filter((artifact) => artifact.kind === "answer");
  await page.goto("/chat");
  await openThread(page);

  await expect(page.getByTestId(`chat-answer-${RUN_ID}`)).toContainText("合成のファイルを作りました。");
  await expect(page.getByRole("region", { name: "作成したファイル" })).toHaveCount(0);
});
