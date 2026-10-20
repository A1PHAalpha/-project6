// Cross-feature UI state shared by the application shell and diagnostics.
import { create } from "zustand";
import type { Device } from "../types";

export interface ContextItem {
  id: string;
  type: "device" | "link";
  label: string;
  detail: string;
}

export interface PendingTerminalCommand {
  command: string;
  source: "manual" | "quick_action";
}

export interface AnchoredRect {
  top: number;
  right: number;
  bottom: number;
  left: number;
  width: number;
  height: number;
}

interface WorkspaceState {
  sidebarCollapsed: boolean;
  deviceDrawer: Device | null;
  deviceDrawerAnchor: AnchoredRect | null;
  contexts: ContextItem[];
  terminalDeviceName: string | null;
  pendingTerminalCommands: PendingTerminalCommand[];
  assistantCollapsed: boolean;
  sessionPanelOpen: boolean;
  toggleSidebar: () => void;
  openDeviceDrawer: (device: Device, anchor?: AnchoredRect | null) => void;
  closeDeviceDrawer: () => void;
  addContext: (item: ContextItem) => void;
  removeContext: (id: string) => void;
  openTerminal: (deviceName: string) => void;
  queueTerminalCommands: (deviceName: string, commands: string[]) => void;
  consumeTerminalCommands: () => PendingTerminalCommand[];
  closeTerminal: () => void;
  toggleAssistant: () => void;
  setSessionPanelOpen: (open: boolean) => void;
}

export const useWorkspace = create<WorkspaceState>((set) => ({
  sidebarCollapsed: false,
  deviceDrawer: null,
  deviceDrawerAnchor: null,
  contexts: [],
  terminalDeviceName: null,
  pendingTerminalCommands: [],
  assistantCollapsed: false,
  sessionPanelOpen: false,
  toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
  openDeviceDrawer: (device, anchor = null) => set({ deviceDrawer: device, deviceDrawerAnchor: anchor }),
  closeDeviceDrawer: () => set({ deviceDrawer: null, deviceDrawerAnchor: null }),
  addContext: (item) =>
    set((state) => ({
      contexts: state.contexts.some((current) => current.id === item.id)
        ? state.contexts
        : [...state.contexts, item]
    })),
  removeContext: (id) => set((state) => ({ contexts: state.contexts.filter((item) => item.id !== id) })),
  openTerminal: (deviceName) => set({ terminalDeviceName: deviceName, pendingTerminalCommands: [] }),
  queueTerminalCommands: (deviceName, commands) =>
    set({
      terminalDeviceName: deviceName,
      pendingTerminalCommands: commands.map((command) => ({
        command,
        source: "quick_action" as const
      }))
    }),
  consumeTerminalCommands: () => {
    let pending: PendingTerminalCommand[] = [];
    set((state) => {
      pending = state.pendingTerminalCommands;
      return { pendingTerminalCommands: [] };
    });
    return pending;
  },
  closeTerminal: () => set({ terminalDeviceName: null, pendingTerminalCommands: [] }),
  toggleAssistant: () => set((state) => ({ assistantCollapsed: !state.assistantCollapsed })),
  setSessionPanelOpen: (open) => set({ sessionPanelOpen: open })
}));
