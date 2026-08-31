import { expect, test } from "@playwright/test";

const formUrl = process.env.OCI_RESOURCE_MANAGER_ADB_FORM_URL;

const EVERYWHERE_ACCESS = "すべての場所からのセキュア・アクセス";
const ALLOWED_ACCESS = "許可されたIPおよびVCN限定のセキュア・アクセス";
const PRIVATE_ACCESS = "プライベート・エンドポイント・アクセスのみ";
const IP_OR_CIDR = "IPアドレスまたはCIDRブロック";
const CREATE_NEW_MODE = "新規 Autonomous AI Database の作成";
const USE_EXISTING_MODE = "既存の Autonomous AI Database を選択";

test.describe("OCI Resource Manager ADB作成フォーム", () => {
  test.skip(
    !formUrl,
    "OCI_RESOURCE_MANAGER_ADB_FORM_URLを指定した認証済みテスト・テナンシで実行します。",
  );

  test("ADBの選択順、既定値、ネットワーク項目の段階表示が契約に一致する", async ({ page }) => {
    await page.goto(formUrl!, { waitUntil: "domcontentloaded" });

    const adbCompartment = page.getByLabel("ADBのコンパートメント", { exact: true });
    const deploymentMode = page.getByLabel("ADBの利用方法", { exact: true });
    await expect(adbCompartment).toBeVisible();
    await expect(deploymentMode).toBeVisible();
    await expect(deploymentMode).toHaveValue(CREATE_NEW_MODE);

    const [compartmentBox, deploymentModeBox] = await Promise.all([
      adbCompartment.boundingBox(),
      deploymentMode.boundingBox(),
    ]);
    expect(compartmentBox).not.toBeNull();
    expect(deploymentModeBox).not.toBeNull();
    expect(compartmentBox!.y).toBeLessThan(deploymentModeBox!.y);

    await expect(page.getByText("ネットワーク・アクセス", { exact: true })).toBeVisible();

    const workload = page.getByLabel("Workload type", { exact: true });
    await expect(workload).toHaveValue("LH");

    const accessType = page.getByLabel("アクセス・タイプ", { exact: true });
    await expect(accessType).toHaveValue(PRIVATE_ACCESS);
    await expect(accessType.locator("option")).toHaveText([
      EVERYWHERE_ACCESS,
      ALLOWED_ACCESS,
      PRIVATE_ACCESS,
    ]);

    await expect(page.getByLabel("VCNのコンパートメント", { exact: true }).first()).toBeVisible();
    await expect(page.getByLabel("仮想クラウド・ネットワーク", { exact: true })).toBeVisible();
    await expect(page.getByLabel("サブネットのコンパートメント", { exact: true }).first()).toBeVisible();
    await expect(page.getByLabel("サブネット", { exact: true })).toBeVisible();

    await deploymentMode.selectOption({ label: USE_EXISTING_MODE });
    await expect(page.getByLabel("既存のAutonomous AI Database", { exact: true })).toBeVisible();
    await expect(workload).toBeHidden();
    await expect(page.getByText("ネットワーク・アクセス", { exact: true })).toBeHidden();

    await deploymentMode.focus();
    await deploymentMode.press("Home");
    await expect(deploymentMode).toHaveValue(CREATE_NEW_MODE);
    await expect(workload).toBeVisible();
    await expect(page.getByText("ネットワーク・アクセス", { exact: true })).toBeVisible();

    await accessType.selectOption({ label: EVERYWHERE_ACCESS });
    await expect(page.getByLabel("仮想クラウド・ネットワーク", { exact: true })).toBeHidden();
    await expect(page.getByLabel("IP表記法タイプ", { exact: true })).toBeHidden();

    await accessType.selectOption({ label: ALLOWED_ACCESS });
    const notationType = page.getByLabel("IP表記法タイプ", { exact: true });
    await expect(notationType).toBeVisible();
    await expect(notationType).toHaveValue("VCN");
    await expect(page.getByLabel("許可する仮想クラウド・ネットワーク", { exact: true })).toBeVisible();

    await notationType.selectOption({ label: IP_OR_CIDR });
    await expect(
      page.getByLabel("許可するIPアドレスまたはCIDRブロック", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByLabel("許可する仮想クラウド・ネットワーク", { exact: true }),
    ).toBeHidden();

    await accessType.focus();
    await accessType.press("End");
    await expect(accessType).toHaveValue(PRIVATE_ACCESS);
    await expect(page.getByLabel("サブネット", { exact: true })).toBeVisible();

    const hasHorizontalOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    );
    expect(hasHorizontalOverflow).toBe(false);
  });
});
