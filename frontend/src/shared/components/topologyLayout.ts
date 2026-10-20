// Pure topology layout helpers.
import type { Topology, TopologyNode } from "../types";

export interface PositionedTopologyNode {
  node: TopologyNode;
  x: number;
  y: number;
}

export interface TopologySiteFrame {
  site: string;
  x: number;
  y: number;
  width: number;
  height: number;
  nodeCount: number;
}

const GROUP_GAP = 46;
const GROUP_WIDTH = 292;
const GROUP_HEADER = 48;
const NODE_WIDTH = 196;
const NODE_HEIGHT = 116;
const NODE_GAP_X = 18;
const NODE_GAP_Y = 24;
const NODE_LEFT_PADDING = 14;

function roleRank(node: TopologyNode) {
  const text = `${node.role} ${node.deviceName}`.toLowerCase();
  if (/router|路由器|r\d/.test(text)) return 0;
  if (/switch|交换机|sw\d/.test(text)) return 1;
  if (/server|服务器|srv/.test(text)) return 2;
  return 3;
}

/**
 * Build a deterministic layout from the captured graph. Site grouping and
 * node ordering come from live device definitions and topology degree, so new
 * sites/devices appear automatically instead of relying on static SVG points.
 */
export function buildTopologyLayout(topology: Topology): {
  nodes: PositionedTopologyNode[];
  sites: TopologySiteFrame[];
} {
  const degree = new Map<string, number>();
  for (const edge of topology.edges) {
    degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1);
    degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1);
  }
  const siteOrder = Array.from(new Set(topology.nodes.map((node) => node.site)));
  siteOrder.sort((left, right) => {
    if (left.includes("总部") && !right.includes("总部")) return -1;
    if (right.includes("总部") && !left.includes("总部")) return 1;
    return left.localeCompare(right, "zh-CN");
  });

  const sites: TopologySiteFrame[] = [];
  const positioned: PositionedTopologyNode[] = [];
  let groupX = 18;
  let maxHeight = GROUP_HEADER;

  for (const site of siteOrder) {
    const siteNodes = topology.nodes
      .filter((node) => node.site === site)
      .sort((left, right) => {
        const roleDelta = roleRank(left) - roleRank(right);
        if (roleDelta !== 0) return roleDelta;
        const degreeDelta = (degree.get(right.id) ?? 0) - (degree.get(left.id) ?? 0);
        if (degreeDelta !== 0) return degreeDelta;
        return left.deviceName.localeCompare(right.deviceName, "zh-CN");
      });
    const columns = siteNodes.length > 1 ? 2 : 1;
    const rows = Math.max(1, Math.ceil(siteNodes.length / columns));
    const contentWidth = columns * NODE_WIDTH + (columns - 1) * NODE_GAP_X;
    const groupWidth = Math.max(GROUP_WIDTH, contentWidth + NODE_LEFT_PADDING * 2);
    const groupHeight = GROUP_HEADER + rows * NODE_HEIGHT + (rows - 1) * NODE_GAP_Y + 20;
    maxHeight = Math.max(maxHeight, groupHeight);
    sites.push({
      site,
      x: groupX,
      y: 18,
      width: groupWidth,
      height: groupHeight,
      nodeCount: siteNodes.length
    });
    siteNodes.forEach((node, index) => {
      const column = index % columns;
      const row = Math.floor(index / columns);
      positioned.push({
        node,
        x: groupX + NODE_LEFT_PADDING + column * (NODE_WIDTH + NODE_GAP_X),
        y: 18 + GROUP_HEADER + row * (NODE_HEIGHT + NODE_GAP_Y)
      });
    });
    groupX += groupWidth + GROUP_GAP;
  }

  for (const site of sites) site.height = maxHeight;
  return { nodes: positioned, sites };
}
