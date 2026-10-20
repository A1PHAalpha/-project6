import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BackupReminder, StatusBadge } from "./ui";

describe("shared UI semantics", () => {
  it("renders a text label with status color semantics", () => {
    render(<StatusBadge tone="danger">不可达</StatusBadge>);
    expect(screen.getByText("不可达")).toBeVisible();
  });

  it("shows an explicit backup warning before terminal operations", () => {
    render(<BackupReminder deviceName="BR-A-R2" />);
    expect(screen.getByText("操作前请确认配置备份")).toBeVisible();
    expect(screen.getByText("BR-A-R2", { exact: false })).toBeVisible();
  });
});
