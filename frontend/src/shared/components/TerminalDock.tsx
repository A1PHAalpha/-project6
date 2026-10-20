// Shared audited terminal overlay.
import {
  CheckCircle2,
  ChevronDown,
  DatabaseBackup,
  LoaderCircle,
  SquareTerminal,
  X
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { useWorkspace } from "../state/workspace";
import type { TerminalEvent, TerminalSession } from "../types";
import { BackupReminder, ConfirmDialog } from "./ui";

const writeCommandPattern =
  /^(configure|conf t|interface|no shut|shutdown|write|copy run|vlan|switchport|ip route|access-list|router|erase|delete|reload)/i;

export function TerminalDock() {
  const deviceName = useWorkspace((state) => state.terminalDeviceName);
  const closeTerminal = useWorkspace((state) => state.closeTerminal);
  const consumeTerminalCommands = useWorkspace((state) => state.consumeTerminalCommands);
  const [backupAcknowledged, setBackupAcknowledged] = useState(false);
  const [session, setSession] = useState<TerminalSession | null>(null);
  const [events, setEvents] = useState<TerminalEvent[]>([]);
  const [starting, setStarting] = useState(false);
  const [sending, setSending] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [command, setCommand] = useState("");
  const [pendingWriteCommand, setPendingWriteCommand] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setBackupAcknowledged(false);
    setSession(null);
    setEvents([]);
    setCommand("");
    setError(null);
    setCollapsed(false);
  }, [deviceName]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [events]);

  useEffect(() => {
    if (!session) return;
    const pending = consumeTerminalCommands();
    if (!pending.length) return;
    void (async () => {
      for (const pendingCommand of pending) {
        await dispatchCommand(pendingCommand.command, pendingCommand.source);
      }
    })();
    // The queued commands are consumed exactly once for a newly created session.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.id]);

  if (!deviceName) return null;
  const targetDeviceName = deviceName;

  async function startSession() {
    setStarting(true);
    setError(null);
    try {
      const created = await api.startTerminal(targetDeviceName, backupAcknowledged);
      setSession(created);
      setEvents(created.events);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "终端会话创建失败");
    } finally {
      setStarting(false);
    }
  }

  async function dispatchCommand(rawCommand: string, source: "manual" | "quick_action" = "manual") {
    const trimmed = rawCommand.trim();
    if (!trimmed || !session) return;
    setSending(true);
    setError(null);
    try {
      const received = await api.sendTerminalCommand(session.id, trimmed, {
        source,
        confirmed: source === "quick_action"
      });
      setEvents((current) => [...current, ...received]);
      setCommand("");
    } catch (cause) {
      setEvents((current) => [
        ...current,
        {
          id: `error_${Date.now()}`,
          sequence: current.length + 1,
          kind: "error",
          content: cause instanceof Error ? cause.message : "命令下发失败",
          createdAt: new Date().toISOString()
        }
      ]);
    } finally {
      setSending(false);
    }
  }

  function submitCommand() {
    const trimmed = command.trim();
    if (!trimmed) return;
    if (writeCommandPattern.test(trimmed)) {
      setPendingWriteCommand(trimmed);
      return;
    }
    void dispatchCommand(trimmed, "manual");
  }

  async function close() {
    if (session) {
      try {
        await api.closeTerminal(session.id);
      } catch {
        // The local transcript remains visible until the dock closes.
      }
    }
    closeTerminal();
  }

  return (
    <>
      <aside className={`terminal-dock ${collapsed ? "collapsed" : ""}`} aria-label="设备终端">
        <header className="terminal-head">
          <div className="terminal-heading">
            <SquareTerminal size={15} />
            <strong>终端 · {deviceName}</strong>
            <span className={`status-badge ${session ? "tone-success" : "tone-warning"}`}>
              <span className="status-dot" />
              {session ? "已记录" : "待备份确认"}
            </span>
          </div>
          <div className="terminal-head-actions">
            <button
              className="icon-button"
              type="button"
              aria-label={collapsed ? "展开终端" : "收起终端"}
              onClick={() => setCollapsed((current) => !current)}
            >
              <ChevronDown size={15} />
            </button>
            <button className="icon-button" type="button" aria-label="关闭终端" onClick={() => void close()}>
              <X size={15} />
            </button>
          </div>
        </header>

        {!collapsed ? (
          <>
            {!session ? (
              <div className="terminal-start">
                <BackupReminder deviceName={deviceName} />
                <label className="check-row">
                  <input
                    type="checkbox"
                    checked={backupAcknowledged}
                    onChange={(event) => setBackupAcknowledged(event.target.checked)}
                  />
                  <span>我已确认当前运行配置已备份，并理解终端操作会被全程记录</span>
                </label>
                {error ? <div className="inline-error">{error}</div> : null}
                <button
                  className="button button-primary"
                  type="button"
                  disabled={!backupAcknowledged || starting}
                  onClick={() => void startSession()}
                >
                  {starting ? <LoaderCircle className="spin" size={15} /> : <DatabaseBackup size={15} />}
                  建立受审计终端
                </button>
              </div>
            ) : (
              <>
                <div className="terminal-transcript" ref={scrollRef}>
                  {events.map((event) => (
                    <div className={`terminal-line line-${event.kind}`} key={event.id}>
                      <span>{event.kind === "command" ? "$" : event.kind === "error" ? "!" : "·"}</span>
                      <pre>{event.content}</pre>
                    </div>
                  ))}
                  {sending ? (
                    <div className="terminal-line line-system">
                      <span>·</span>
                      <pre>等待设备输出…</pre>
                    </div>
                  ) : null}
                </div>
                <footer className="terminal-input-row">
                  <div className="terminal-audit-note">
                    <CheckCircle2 size={13} />
                    命令与输出正在记录
                  </div>
                  <input
                    className="field"
                    value={command}
                    placeholder="输入命令，Enter 发送；写操作会二次确认"
                    onChange={(event) => setCommand(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" && !event.nativeEvent.isComposing) submitCommand();
                    }}
                  />
                  <button
                    className="button button-primary button-sm"
                    type="button"
                    disabled={sending || !command.trim()}
                    onClick={submitCommand}
                  >
                    发送
                  </button>
                </footer>
              </>
            )}
          </>
        ) : null}
      </aside>

      <ConfirmDialog
        open={Boolean(pendingWriteCommand)}
        onOpenChange={(open) => {
          if (!open) setPendingWriteCommand(null);
        }}
        title="确认下发写操作命令"
        description="终端允许执行全量操作。该命令可能修改设备配置，请再次确认备份、影响范围和回滚方案。"
        confirmLabel="确认并下发"
        tone="danger"
        onConfirm={async () => {
          const target = pendingWriteCommand;
          setPendingWriteCommand(null);
          if (target) await dispatchCommand(target, "manual");
        }}
      >
        <BackupReminder destructive deviceName={deviceName} />
        <pre className="command-preview">{pendingWriteCommand}</pre>
      </ConfirmDialog>
    </>
  );
}
