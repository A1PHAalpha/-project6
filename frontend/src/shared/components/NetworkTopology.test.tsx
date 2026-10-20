import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { mockTopology } from "../mocks/data";
import { NetworkTopology } from "./NetworkTopology";

describe("NetworkTopology", () => {
  it("renders inventory devices and preserves unknown state", () => {
    render(<NetworkTopology topology={mockTopology} mode="summary" />);
    expect(screen.getByText("BR-A-R2")).toBeInTheDocument();
    expect(screen.getByText("HQ-SRV")).toBeInTheDocument();
    expect(screen.getByText("未采集")).toBeInTheDocument();
    expect(screen.getByText("点击进入诊断中心")).toBeVisible();
  });
});
