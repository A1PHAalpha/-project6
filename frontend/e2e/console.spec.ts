import { expect, test } from "@playwright/test";

test("overview and shared dialogs remain usable", async ({ page }, testInfo) => {
  await page.goto("/overview");
  await expect(page.getByRole("heading", { name: "网络运行总览" })).toBeVisible();
  await expect(page.getByText("设备可达").first()).toBeVisible();
  await expect(page.getByText("当前捕获的问题")).toBeVisible();
  const minimumFontSize = await page.locator(".data-table td").first().evaluate(
    (element) => Number.parseFloat(getComputedStyle(element).fontSize)
  );
  expect(minimumFontSize).toBeGreaterThanOrEqual(13);

  if (testInfo.project.name === "mobile-chrome") {
    await page.getByRole("button", { name: "打开导航" }).click();
  }
  await page.getByRole("link", { name: "设备与采集" }).click();
  await expect(page.getByRole("heading", { name: "设备与采集" })).toBeVisible();
  await page.getByRole("button", { name: "详情", exact: true }).first().click();
  await expect(page.getByRole("heading", { name: "BR-A-PC" })).toBeVisible();
  await page.getByRole("button", { name: "打开终端" }).click();
  await expect(page.getByRole("heading", { name: "打开设备终端" })).toBeVisible();
  await page.getByRole("button", { name: "继续打开终端" }).click();
  await expect(page.getByText("操作前请确认配置备份")).toBeVisible();

  const startButton = page.getByRole("button", { name: "建立受审计终端" });
  await expect(startButton).toBeDisabled();
  await page.getByText("我已确认当前运行配置已备份").click();
  await expect(startButton).toBeEnabled();
});

test("diagnosis requires manual acknowledgement before quick dispatch", async ({ page }) => {
  await page.goto("/diagnosis?session=session_demo_branch_a");
  await expect(page.getByRole("heading", { name: "诊断中心" })).toBeVisible();
  await page.getByRole("button", { name: "确认后快速执行" }).click();
  await expect(page.getByRole("heading", { name: "确认快速执行诊断命令" })).toBeVisible();
  const confirmButton = page.getByRole("button", { name: "确认并下发" });
  await expect(confirmButton).toBeDisabled();
  await page.getByText("我已阅读命令说明并确认当前配置备份状态").click();
  await expect(confirmButton).toBeEnabled();
  await confirmButton.click();
  await expect(page.getByRole("button", { name: "建立受审计终端" })).toBeVisible();
});

test("idle assistant is empty, scrollable and keeps model capabilities in the composer", async ({ page }, testInfo) => {
  await page.goto("/diagnosis");
  const assistant = page.getByRole("complementary", { name: "AI 运维助手" });
  await expect(assistant).toBeVisible();
  await expect(assistant.getByText("诊断结论")).toHaveCount(0);
  await expect(assistant.getByRole("button", { name: "知识库 开" })).toBeVisible();
  await expect(assistant.getByRole("button", { name: "网络搜索 未接入" })).toBeDisabled();
  await expect(assistant.getByPlaceholder("描述故障现象，或输入复测 / 查看接口…")).toBeVisible();
  if (testInfo.project.name !== "mobile-chrome") {
    const assistantBox = await assistant.boundingBox();
    expect(assistantBox).not.toBeNull();
    await expect
      .poll(async () => {
        const deviceBoxes = await page.locator(".topology-device").evaluateAll((elements) =>
          elements.map((element) => element.getBoundingClientRect().right)
        );
        return Math.max(...deviceBoxes);
      })
      .toBeLessThan(assistantBox!.x);
    const resize = assistant.getByRole("button", { name: "调整助手宽度" });
    await expect(resize).toBeVisible();
    const before = await assistant.boundingBox();
    const handle = await resize.boundingBox();
    expect(before).not.toBeNull();
    expect(handle).not.toBeNull();
    await page.mouse.move(handle!.x + 3, handle!.y + handle!.height / 2);
    await page.mouse.down();
    await page.mouse.move(handle!.x - 90, handle!.y + handle!.height / 2);
    await page.mouse.up();
    const after = await assistant.boundingBox();
    expect(after!.width).toBeGreaterThan(before!.width + 50);
  }
});

test("device context is represented as an @ mention inside the composer", async ({ page }, testInfo) => {
  await page.goto("/assets");
  await page.getByRole("button", { name: "详情", exact: true }).first().click();
  const drawer = page.getByRole("complementary", { name: /BR-A-PC 设备详情/ });
  await expect(drawer).toBeVisible();
  const box = await drawer.boundingBox();
  const viewport = page.viewportSize();
  expect(box).not.toBeNull();
  expect(viewport).not.toBeNull();
  expect(box!.height).toBeLessThan(viewport!.height);
  await drawer.getByRole("button", { name: "@ BR-A-PC" }).click();
  if (testInfo.project.name === "mobile-chrome") {
    await page.getByRole("button", { name: "打开导航" }).click();
  }
  await page.getByRole("link", { name: "诊断中心" }).click();
  await expect(page.getByText("@BR-A-PC")).toBeVisible();
});

test("assistant and device detail card can be repositioned by dragging", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === "mobile-chrome", "resizable floating desktop surfaces");
  await page.goto("/diagnosis");
  const assistant = page.getByRole("complementary", { name: "AI 运维助手" });
  const header = assistant.locator(".ai-head");
  const before = await assistant.boundingBox();
  const dragStart = await header.boundingBox();
  expect(before).not.toBeNull();
  expect(dragStart).not.toBeNull();
  await page.mouse.move(dragStart!.x + 120, dragStart!.y + 18);
  await page.mouse.down();
  await page.mouse.move(dragStart!.x + 40, dragStart!.y + 110, { steps: 8 });
  await page.mouse.up();
  const after = await assistant.boundingBox();
  expect(after!.x).toBeLessThan(before!.x - 40);
  expect(after!.y).toBeGreaterThan(before!.y + 40);

  const viewport = page.locator(".react-flow__viewport").first();
  const expandedTransform = await viewport.getAttribute("style");
  await assistant.getByRole("button", { name: "收起 AI 运维助手" }).click();
  await expect
    .poll(() => viewport.getAttribute("style"))
    .not.toBe(expandedTransform);
  const collapsed = page.getByRole("complementary", { name: "AI 运维助手" });
  const collapsedHeader = collapsed.locator(".ai-head");
  const collapsedBefore = await collapsed.boundingBox();
  const collapsedHandle = await collapsedHeader.boundingBox();
  await page.mouse.move(collapsedHandle!.x + 100, collapsedHandle!.y + 16);
  await page.mouse.down();
  await page.mouse.move(collapsedHandle!.x + 10, collapsedHandle!.y - 60, { steps: 8 });
  await page.mouse.up();
  const collapsedAfter = await collapsed.boundingBox();
  expect(collapsedAfter!.x).toBeLessThan(collapsedBefore!.x - 40);
  expect(collapsedAfter!.y).toBeLessThan(collapsedBefore!.y - 20);

  await page.goto("/assets");
  await page.getByRole("button", { name: "详情", exact: true }).first().click();
  const detail = page.getByRole("complementary", { name: /BR-A-PC 设备详情/ });
  const detailBefore = await detail.boundingBox();
  const detailHeader = await detail.locator(".drawer-head").boundingBox();
  await page.mouse.move(detailHeader!.x + 80, detailHeader!.y + 18);
  await page.mouse.down();
  await page.mouse.move(detailHeader!.x + 20, detailHeader!.y - 30, { steps: 8 });
  await page.mouse.up();
  const detailAfter = await detail.boundingBox();
  expect(detailAfter!.x).toBeLessThan(detailBefore!.x - 20);
  expect(detailAfter!.y).toBeLessThan(detailBefore!.y - 10);
});

test("knowledge preview and rebuild workflow render", async ({ page }) => {
  await page.goto("/knowledge");
  await expect(page.getByRole("heading", { name: "运维知识库" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "接口 administratively down 手动恢复" })).toBeVisible();
  await page.getByRole("button", { name: "2 项待构建" }).click();
  await expect(page.getByRole("heading", { name: "重构知识库" })).toBeVisible();
  await expect(page.getByText("旧索引保持可用", { exact: false })).toBeVisible();
});

test("settings mask secrets and collection schedule is editable", async ({ page }, testInfo) => {
  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "设置" })).toBeVisible();
  const apiKey = page.getByLabel("API Key");
  await expect(apiKey).toHaveAttribute("type", "password");
  await expect(apiKey).toHaveValue(/••/);
  await page.getByRole("button", { name: "保存设置" }).click();
  await expect(page.getByText("请重启服务使配置生效")).toBeVisible();

  if (testInfo.project.name === "mobile-chrome") {
    await page.getByRole("button", { name: "打开导航" }).click();
  }
  await page.getByRole("link", { name: "设备与采集" }).click();
  await page.getByRole("button", { name: "采集设置" }).click();
  await expect(page.getByRole("heading", { name: "采集设置" })).toBeVisible();
  await page.getByLabel("采集频率（分钟）").fill("30");
  await page.getByRole("button", { name: "保存采集计划" }).click();
  await expect(page.getByText("采集计划已保存", { exact: false })).toBeVisible();
});

test("mobile navigation opens as a drawer", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-chrome", "mobile-only assertion");
  await page.goto("/overview");
  await page.getByRole("button", { name: "打开导航" }).click();
  await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
  await page.getByRole("link", { name: "诊断中心" }).click();
  await expect(page.getByRole("heading", { name: "诊断中心" })).toBeVisible();
});
