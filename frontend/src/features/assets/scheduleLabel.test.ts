import { describe, expect, it } from "vitest";

import { scheduleLabel } from "./scheduleLabel";

describe("scheduleLabel", () => {
  it("does not claim auto collection when scheduler is unavailable", () => {
    const label = scheduleLabel({
      enabled: true,
      scheduler_available: false,
      frequency_minutes: 30,
      active_window: { start: "00:00", end: "23:59" },
      scope: "all",
      concurrency: 4,
      timeout_seconds: 15,
      retries: 1,
      channels: {}
    });
    expect(label.active).toBe(false);
    expect(label.title).toContain("未启用");
  });
});
