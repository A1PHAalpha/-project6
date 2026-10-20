// Shared device context drawer.
import {
  Activity,
  ChevronLeft,
  ChevronRight,
  Database,
  ExternalLink,
  GripVertical,
  Layers3,
  Plus,
  ScrollText,
  Server,
  SquareTerminal,
  X
} from "lucide-react";
import { useEffect, useMemo, useState, type CSSProperties, type MouseEvent as ReactMouseEvent } from "react";
import { useWorkspace } from "../state/workspace";
import type { DeviceFact } from "../types";
import { BackupReminder, ConfirmDialog, StatusBadge } from "./ui";

type DetailTab = "facts" | "interfaces" | "history" | "raw";

function reachabilityTone(reachability: "reachable" | "unreachable" | "unknown") {
  return reachability === "reachable" ? "success" : reachability === "unreachable" ? "danger" : "neutral";
}

function formatTime(value: string | null) {
  return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "未采集";
}

function factMatchesInterface(fact: DeviceFact, interfaceName: string) {
  const shortName = interfaceName
    .replace(/GigabitEthernet/gi, "Gi")
    .replace(/Ethernet/gi, "Eth")
    .toLowerCase();
  return fact.label.toLowerCase().includes(shortName) || interfaceName.toLowerCase().includes(fact.label.toLowerCase());
}

export function DeviceDetailDrawer() {
  const device = useWorkspace((state) => state.deviceDrawer);
  const anchor = useWorkspace((state) => state.deviceDrawerAnchor);
  const close = useWorkspace((state) => state.closeDeviceDrawer);
  const addContext = useWorkspace((state) => state.addContext);
  const openTerminal = useWorkspace((state) => state.openTerminal);
  const [activeTab, setActiveTab] = useState<DetailTab>("facts");
  const [factPage, setFactPage] = useState(0);
  const [terminalReminderOpen, setTerminalReminderOpen] = useState(false);
  const [manualPosition, setManualPosition] = useState<{ x: number; y: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const [viewport, setViewport] = useState(() => ({
    width: window.innerWidth,
    height: window.innerHeight
  }));
  const pageSize = 3;
  const pages = useMemo(
    () => (device ? Math.max(1, Math.ceil(device.facts.length / pageSize)) : 1),
    [device]
  );

  useEffect(() => {
    const resize = () => setViewport({ width: window.innerWidth, height: window.innerHeight });
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);

  useEffect(() => {
    setFactPage(0);
    setActiveTab("facts");
    setManualPosition(null);
  }, [device?.id]);

  useEffect(() => {
    if (!device) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [close, device]);

  if (!device) return null;

  const isMobile = viewport.width < 760;
  const width = Math.min(448, viewport.width - 24);
  const height = Math.min(690, viewport.height - 24);
  let left = viewport.width - width - 16;
  let top = 74;
  if (anchor) {
    const rightCandidate = anchor.right + 14;
    const leftCandidate = anchor.left - width - 14;
    left = rightCandidate + width <= viewport.width - 12 ? rightCandidate : leftCandidate;
    left = Math.max(12, Math.min(left, viewport.width - width - 12));
    top = anchor.top - 24;
    top = Math.max(12, Math.min(top, viewport.height - height - 12));
  } else if (!isMobile) {
    left = Math.max(12, Math.min(viewport.width - width - 16, left));
  }
  const positionStyle: CSSProperties | undefined = isMobile
    ? undefined
    : {
        left: manualPosition?.x ?? left,
        top: manualPosition?.y ?? top,
        width,
        maxHeight: height
      };

  function startDrag(event: ReactMouseEvent<HTMLElement>) {
    if (isMobile || (event.target as HTMLElement).closest("button")) return;
    const panel = event.currentTarget.closest(".device-popover") as HTMLElement | null;
    if (!panel) return;
    event.preventDefault();
    const panelRect = panel.getBoundingClientRect();
    const startX = event.clientX;
    const startY = event.clientY;
    const startLeft = panelRect.left;
    const startTop = panelRect.top;
    setDragging(true);
    const move = (moveEvent: MouseEvent) => {
      setManualPosition({
        x: Math.max(12, Math.min(viewport.width - panelRect.width - 12, startLeft + moveEvent.clientX - startX)),
        y: Math.max(12, Math.min(viewport.height - panelRect.height - 12, startTop + moveEvent.clientY - startY))
      });
    };
    const stop = () => {
      setDragging(false);
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", stop);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", stop, { once: true });
  }

  const visibleFacts = device.facts.slice(factPage * pageSize, (factPage + 1) * pageSize);

  return (
    <>
      <button
        className="detail-click-catcher"
        type="button"
        aria-label="关闭设备详情"
        onClick={close}
      />
      <aside
        className={`device-drawer device-popover ${dragging ? "dragging" : ""}`}
        style={positionStyle}
        aria-label={`${device.name} 设备详情`}
      >
        <header className="drawer-head" onMouseDown={startDrag} title="拖动调整位置">
          <div>
            <div className="drawer-kicker">
              <Server size={15} />
              设备详情
            </div>
            <h2>{device.name}</h2>
            <p className="drawer-summary">
              {device.site} · {device.role} · {formatTime(device.collectedAt)}
            </p>
            <div className="tag-row">
              <StatusBadge tone={reachabilityTone(device.reachability)}>
                {device.reachability === "reachable"
                  ? "可达"
                  : device.reachability === "unreachable"
                    ? "不可达"
                    : "未采集"}
              </StatusBadge>
              <span className="tag">{device.platform}</span>
              <span className="tag mono">{device.managementIp}</span>
            </div>
          </div>
          <div className="drawer-head-actions">
            <GripVertical className="drawer-drag-grip" size={17} aria-hidden="true" />
            <button className="icon-button" type="button" aria-label="关闭" onClick={close}>
              <X size={18} />
            </button>
          </div>
        </header>

        <div className="drawer-meta-grid">
          <div>
            <span>管理地址</span>
            <strong className="mono">{device.managementIp || "未配置"}</strong>
          </div>
          <div>
            <span>最近采集</span>
            <strong>{formatTime(device.collectedAt)}</strong>
          </div>
          <div>
            <span>采集批次</span>
            <strong className="mono" title={device.runId ?? ""}>
              {device.runId ?? "未关联"}
            </strong>
          </div>
          <div>
            <span>管理方式</span>
            <strong>{device.reachability === "unknown" ? "待配置" : "SSH / SNMP"}</strong>
          </div>
        </div>

        <div className="detail-tabs" role="tablist" aria-label="设备详情分区">
          <button role="tab" aria-selected={activeTab === "facts"} className={activeTab === "facts" ? "active" : ""} type="button" onClick={() => setActiveTab("facts")}>
            <Activity size={14} />
            当前事实
          </button>
          <button role="tab" aria-selected={activeTab === "interfaces"} className={activeTab === "interfaces" ? "active" : ""} type="button" onClick={() => setActiveTab("interfaces")}>
            <Layers3 size={14} />
            接口
          </button>
          <button role="tab" aria-selected={activeTab === "history"} className={activeTab === "history" ? "active" : ""} type="button" onClick={() => setActiveTab("history")}>
            <Database size={14} />
            采集历史
          </button>
          <button role="tab" aria-selected={activeTab === "raw"} className={activeTab === "raw" ? "active" : ""} type="button" onClick={() => setActiveTab("raw")}>
            <ScrollText size={14} />
            原始输出
          </button>
        </div>

        <div className="drawer-body detail-tab-body">
          {activeTab === "facts" ? (
            <>
              <div className="fact-list">
                {visibleFacts.length ? (
                  visibleFacts.map((fact) => (
                    <article className={`evidence-card tone-${fact.tone}`} key={fact.id}>
                      <div>
                        <strong>{fact.label}</strong>
                        <StatusBadge tone={fact.tone}>{fact.type}</StatusBadge>
                      </div>
                      <p>{fact.value}</p>
                      <span>{formatTime(fact.observedAt)} · {fact.runId ?? device.runId ?? "未关联批次"}</span>
                    </article>
                  ))
                ) : (
                  <div className="detail-empty">尚无当前事实。未采集不会回填历史成功状态。</div>
                )}
              </div>
              <div className="mini-pagination detail-pagination">
                <span>事实列表 · {device.facts.length} 条</span>
                <div>
                  <button className="button button-quiet button-xs" type="button" disabled={factPage === 0} onClick={() => setFactPage((page) => Math.max(0, page - 1))} aria-label="上一页">
                    <ChevronLeft size={14} />
                    上一页
                  </button>
                  <span>{factPage + 1} / {pages}</span>
                  <button className="button button-quiet button-xs" type="button" disabled={factPage >= pages - 1} onClick={() => setFactPage((page) => Math.min(pages - 1, page + 1))} aria-label="下一页">
                    下一页
                    <ChevronRight size={14} />
                  </button>
                </div>
              </div>
            </>
          ) : null}

          {activeTab === "interfaces" ? (
            <div className="detail-interface-list">
              {device.interfaces.length ? device.interfaces.map((name) => {
                const fact = device.facts.find((item) => factMatchesInterface(item, name));
                return (
                  <div className="detail-interface-row" key={name}>
                    <div>
                      <strong className="mono">{name}</strong>
                      <small>{fact ? fact.label : "尚无独立接口事实"}</small>
                    </div>
                    <StatusBadge tone={fact?.tone ?? "neutral"} dot={Boolean(fact)}>
                      {fact?.value ?? "未采集"}
                    </StatusBadge>
                  </div>
                );
              }) : <div className="detail-empty">该设备未配置接口定义。</div>}
            </div>
          ) : null}

          {activeTab === "history" ? (
            <div className="detail-history-block">
              <article className="detail-history-card">
                <div>
                  <span>最近已发布批次</span>
                  <strong className="mono">{device.runId ?? "尚无可发布批次"}</strong>
                </div>
                <StatusBadge tone={reachabilityTone(device.reachability)}>
                  {device.overallStatus}
                </StatusBadge>
                <p>{formatTime(device.collectedAt)}</p>
              </article>
              <a className="button button-quiet" href={`/device/${encodeURIComponent(device.name)}`} target="_blank" rel="noreferrer">
                <ExternalLink size={15} />
                查看完整采集历史
              </a>
            </div>
          ) : null}

          {activeTab === "raw" ? (
            <div className="detail-raw-block">
              <ScrollText size={22} />
              <strong>原始命令输出单独查看</strong>
              <p>为保持详情浮层轻量，原始输出在新页面中按采集项展示，并在显示前执行脱敏。</p>
              <a className="button button-quiet" href={`/device/${encodeURIComponent(device.name)}`} target="_blank" rel="noreferrer">
                <ExternalLink size={15} />
                打开原始输出
              </a>
            </div>
          ) : null}
        </div>

        <footer className="drawer-actions">
          <button
            className="button button-quiet"
            type="button"
            onClick={() => {
              addContext({
                id: `device:${device.name}`,
                type: "device",
                label: device.name,
                detail: `${device.site} · ${device.managementIp}`
              });
              close();
            }}
          >
            <Plus size={15} />
            <span>@ {device.name}</span>
          </button>
          <button className="button button-primary" type="button" onClick={() => setTerminalReminderOpen(true)}>
            <SquareTerminal size={15} />
            打开终端
          </button>
        </footer>
      </aside>

      <ConfirmDialog
        open={terminalReminderOpen}
        onOpenChange={setTerminalReminderOpen}
        title="打开设备终端"
        description="终端允许全量操作。建立会话前请确认当前运行配置已经备份。"
        confirmLabel="继续打开终端"
        onConfirm={() => {
          setTerminalReminderOpen(false);
          close();
          openTerminal(device.name);
        }}
      >
        <BackupReminder deviceName={device.name} />
      </ConfirmDialog>
    </>
  );
}
