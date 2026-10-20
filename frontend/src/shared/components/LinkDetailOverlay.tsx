// Shared topology link detail overlay.
import { Cable, X } from "lucide-react";
import type { TopologyEdge, TopologyNode } from "../types";
import { StatusBadge } from "./ui";

export function LinkDetailOverlay({
  edge,
  source,
  target,
  onClose
}: {
  edge: TopologyEdge;
  source: TopologyNode;
  target: TopologyNode;
  onClose: () => void;
}) {
  const tone = edge.status === "up" ? "success" : edge.status === "unknown" ? "neutral" : "danger";
  return (
    <aside className="link-detail-overlay">
      <header>
        <span className="drawer-kicker">
          <Cable size={14} />
          链路详情
        </span>
        <button className="icon-button" type="button" aria-label="关闭" onClick={onClose}>
          <X size={15} />
        </button>
      </header>
      <h3>{source.deviceName} ↔ {target.deviceName}</h3>
      <div className="tag-row">
        <StatusBadge tone={tone}>{edge.status === "up" ? "链路正常" : edge.status === "unknown" ? "未知" : "链路异常"}</StatusBadge>
        <span className="tag mono">{edge.label}</span>
      </div>
      <div className="link-endpoint-grid">
        <div>
          <span>左端</span>
          <strong>{source.deviceName}</strong>
          <small>{source.managementIp}</small>
        </div>
        <div>
          <span>右端</span>
          <strong>{target.deviceName}</strong>
          <small>{target.managementIp}</small>
        </div>
      </div>
      <div className="drawer-note">链路状态来自最近一次已发布拓扑快照，不能替代接口详情中的物理/协议状态。</div>
    </aside>
  );
}
