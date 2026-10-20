// Tests for shared workspace state.
import { beforeEach, describe, expect, it } from "vitest";
import { useWorkspace } from "./workspace";

describe("workspace state", () => {
  beforeEach(() => {
    useWorkspace.setState({
      sidebarCollapsed: false,
      deviceDrawer: null,
      contexts: [],
      terminalDeviceName: null,
      pendingTerminalCommands: [],
      assistantCollapsed: false,
      sessionPanelOpen: false
    });
  });

  it("deduplicates context items", () => {
    const state = useWorkspace.getState();
    state.addContext({ id: "device:BR-A-R2", type: "device", label: "BR-A-R2", detail: "分支A" });
    state.addContext({ id: "device:BR-A-R2", type: "device", label: "BR-A-R2", detail: "分支A" });
    expect(useWorkspace.getState().contexts).toHaveLength(1);
  });

  it("consumes queued terminal commands exactly once", () => {
    useWorkspace.getState().queueTerminalCommands("BR-A-R2", ["show version"]);
    expect(useWorkspace.getState().consumeTerminalCommands()).toEqual([
      { command: "show version", source: "quick_action" }
    ]);
    expect(useWorkspace.getState().consumeTerminalCommands()).toEqual([]);
  });
});
