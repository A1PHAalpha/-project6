import { expect, test } from "@playwright/test";

const liveBaseUrl = process.env.LIVE_BASE_URL;
const apiToken = process.env.APP_API_TOKEN;

test("production bundle reads the live v1 API", async ({ page, context }) => {
  test.skip(!liveBaseUrl || !apiToken, "Set LIVE_BASE_URL and APP_API_TOKEN");
  await context.addCookies([
    {
      name: "netops_token",
      value: apiToken!,
      url: liveBaseUrl!,
      httpOnly: true,
      sameSite: "Strict"
    }
  ]);
  await page.goto(`${liveBaseUrl}/console/overview`);
  await expect(page.getByRole("heading", { name: "网络运行总览" })).toBeVisible();
  await expect(page.getByText("实时数据")).toBeVisible();
  await expect(page.getByText("当前捕获的问题")).toBeVisible();
  await expect(page.getByText("6 / 9", { exact: true })).toBeVisible();
});
