// Shared topology visualization.
import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Network } from "lucide-react";
import { useEffect, useMemo, useRef, type MouseEvent as ReactMouseEvent } from "react";
import type { Reachability, Tone, Topology, TopologyNode } from "../types";
import { buildTopologyLayout } from "./topologyLayout";

interface DeviceNodeData extends Record<string, unknown> {
  device: TopologyNode;
  mode: "summary" | "interactive";
}

interface TopologyProps {
  topology: Topology;
  mode?: "summary" | "interactive";
  onDeviceClick?: (device: TopologyNode, event?: ReactMouseEvent) => void;
  onEdgeClick?: (edgeId: string) => void;
  onSurfaceClick?: () => void;
  compact?: boolean;
  reservedRight?: number;
}

function reachabilityTone(value: Reachability): Tone {
  if (value === "reachable") return "success";
  if (value === "unreachable") return "danger";
  return "neutral";
}

function DeviceNode({ data, selected }: NodeProps<Node<DeviceNodeData>>) {
  const { device } = data;
  const tone = reachabilityTone(device.reachability);
  return (
    <div className={`topology-device tone-${tone} ${selected ? "selected" : ""}`}>
      <Handle type="target" position={Position.Left} />
      <div className="topology-device-name">
        <span className="status-dot" />
        {device.deviceName}
      </div>
      <div className="topology-device-role">{device.role}</div>
      <div className="topology-device-ip">{device.managementIp}</div>
      <div className="topology-device-summary">{device.summary}</div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const nodeTypes = { device: DeviceNode };

function TopologyCanvas({
  topology,
  mode = "interactive",
  onDeviceClick,
  onEdgeClick,
  onSurfaceClick,
  compact = false,
  reservedRight = 0
}: TopologyProps) {
  const shellRef = useRef<HTMLDivElement>(null);
  const flow = useReactFlow();
  const layout = useMemo(() => buildTopologyLayout(topology), [topology]);

  const nodes = useMemo<Node[]>(() => {
    const siteNodes: Node[] = layout.sites.map((site) => ({
      id: `site-${site.site}`,
      type: "default",
      position: { x: site.x, y: site.y },
      draggable: false,
      selectable: false,
      data: {
        label: (
          <div className="topology-site-label">
            <strong>{site.site}</strong>
            <span>{site.nodeCount} 台设备</span>
          </div>
        )
      },
      style: {
        width: site.width,
        height: site.height,
        background: "rgba(9, 20, 36, 0.52)",
        border: "1px solid rgba(45, 76, 113, 0.55)",
        borderRadius: 12,
        padding: 0,
        zIndex: 0,
        pointerEvents: "none"
      }
    }));
    const deviceNodes = layout.nodes.map<Node<DeviceNodeData>>(({ node, x, y }) => ({
      id: node.id,
      type: "device",
      position: { x, y },
      draggable: false,
      data: { device: node, mode },
      zIndex: 2
    }));
    return [...siteNodes, ...deviceNodes];
  }, [layout, mode]);

  useEffect(() => {
    const shell = shellRef.current;
    if (!shell) return;
    const fit = () => {
      const width = shell.clientWidth;
      const height = shell.clientHeight;
      if (!width || !height) return;
      const maxX = Math.max(...layout.sites.map((site) => site.x + site.width));
      const maxY = Math.max(...layout.sites.map((site) => site.y + site.height));
      const graphWidth = maxX - 14;
      const graphHeight = maxY;
      const inset = Math.min(Math.max(0, reservedRight), Math.max(0, width - 300));
      const availableWidth = Math.max(260, width - inset - 30);
      const availableHeight = Math.max(220, height - 30);
      const zoom = Math.max(
        0.35,
        Math.min(1.18, availableWidth / graphWidth, availableHeight / graphHeight)
      );
      const offsetX = 15 + Math.max(0, (availableWidth - graphWidth * zoom) / 2) - 18 * zoom;
      const offsetY = 15 + Math.max(0, (availableHeight - graphHeight * zoom) / 2) - 18 * zoom;
      flow.setViewport({ x: offsetX, y: offsetY, zoom }, { duration: compact ? 0 : 180 });
    };
    const frame = window.requestAnimationFrame(fit);
    const observer =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(fit);
    observer?.observe(shell);
    return () => {
      window.cancelAnimationFrame(frame);
      observer?.disconnect();
    };
  }, [compact, flow, layout, reservedRight]);

  const edges = useMemo<Edge[]>(
    () =>
      topology.edges.map((edge) => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        label: compact ? undefined : edge.label,
        animated: edge.status === "warning",
        className: `topology-edge edge-${edge.status}`,
        style: {
          stroke:
            edge.status === "down" || edge.status === "warning"
              ? "var(--red)"
              : edge.status === "unknown"
                ? "var(--text-3)"
                : "var(--green)",
          strokeWidth: 1.8,
          strokeDasharray: edge.status === "down" ? "5 5" : undefined
        },
        labelStyle: { fill: "var(--text-3)", fontSize: 13 },
        labelBgStyle: { fill: "var(--bg)", fillOpacity: 0.86 }
      })),
    [compact, topology.edges]
  );

  return (
    <div
      ref={shellRef}
      className={`topology-shell ${compact ? "compact" : ""}`}
      onClick={(event) => {
        if (mode === "summary" || event.target === event.currentTarget) onSurfaceClick?.();
      }}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        minZoom={0.32}
        maxZoom={1.45}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={mode === "interactive"}
        panOnDrag={mode === "interactive"}
        zoomOnScroll={mode === "interactive"}
        zoomOnPinch={mode === "interactive"}
        preventScrolling={false}
        onNodeClick={(event, node) => {
          if (node.type !== "device" || !onDeviceClick) return;
          onDeviceClick((node.data as DeviceNodeData).device, event);
        }}
        onEdgeClick={(_, edge) => onEdgeClick?.(edge.id)}
        proOptions={{ hideAttribution: true }}
      >
        <Background color="#19304a" gap={24} size={1} />
        {mode === "interactive" ? <Controls showInteractive={false} /> : null}
      </ReactFlow>
      {mode === "summary" ? (
        <div className="topology-overlay-label">
          <Network size={14} />
          点击进入诊断中心
        </div>
      ) : null}
    </div>
  );
}

export function NetworkTopology(props: TopologyProps) {
  return (
    <ReactFlowProvider>
      <TopologyCanvas {...props} />
    </ReactFlowProvider>
  );
}
