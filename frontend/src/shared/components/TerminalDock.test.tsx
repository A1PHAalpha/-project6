import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  startTerminal: vi.fn(),
  sendTerminalCommand: vi.fn(),
  closeTerminal: vi.fn()
}));

vi.mock("../api/client", () => ({
  api: {
    startTerminal: mocks.startTerminal,
    sendTerminalCommand: mocks.sendTerminalCommand,
    closeTerminal: mocks.closeTerminal
  }
}));

import { useWorkspace } from "../state/workspace";
import { TerminalDock } from "./TerminalDock";

describe("TerminalDock safety and audit flow", () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    useWorkspace.setState({
      terminalDeviceName: "BR-A-R2",
      pendingTerminalCommands: []
    });
    mocks.startTerminal.mockResolvedValue({
      id: "term_test",
      deviceName: "BR-A-R2",
      operator: "web",
      status: "open",
      backupAcknowledgedAt: new Date().toISOString(),
      startedAt: new Date().toISOString(),
      endedAt: null,
      events: []
    });
    mocks.sendTerminalCommand.mockResolvedValue([]);
  });

  it("cannot open a terminal before the backup acknowledgement", () => {
    render(<TerminalDock />);
    const start = screen.getByRole("button", { name: /建立受审计终端/ });
    expect(start).toBeDisabled();
    expect(screen.getByText("操作前请确认配置备份")).toBeVisible();
    expect(mocks.startTerminal).not.toHaveBeenCalled();
  });

  it("requires a second confirmation before sending a write command", async () => {
    render(<TerminalDock />);
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: /建立受审计终端/ }));
    await waitFor(() => expect(mocks.startTerminal).toHaveBeenCalledWith("BR-A-R2", true));

    fireEvent.change(screen.getByPlaceholderText(/输入命令/), {
      target: { value: "configure terminal" }
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    expect(screen.getByText("确认下发写操作命令")).toBeVisible();
    expect(mocks.sendTerminalCommand).not.toHaveBeenCalled();
  });

  it("marks queued quick actions as confirmed quick-action audit events", async () => {
    useWorkspace.setState({
      pendingTerminalCommands: [
        { command: "show interface GigabitEthernet0/1", source: "quick_action" }
      ]
    });
    render(<TerminalDock />);
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: /建立受审计终端/ }));

    await waitFor(() =>
      expect(mocks.sendTerminalCommand).toHaveBeenCalledWith(
        "term_test",
        "show interface GigabitEthernet0/1",
        { source: "quick_action", confirmed: true }
      )
    );
  });
});
