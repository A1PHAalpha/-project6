import { describe, expect, it } from "vitest";
import { mockTopology } from "../mocks/data";
import { buildTopologyLayout } from "./topologyLayout";

describe("dynamic topology layout", () => {
  it("derives site frames and positioned nodes from captured topology", () => {
    const layout = buildTopologyLayout(mockTopology);
    expect(layout.sites).toHaveLength(3);
    expect(layout.sites[0].site).toBe("总部");
    expect(layout.sites.every((site) => site.nodeCount === 3)).toBe(true);
    expect(layout.nodes).toHaveLength(mockTopology.nodes.length);
    expect(new Set(layout.nodes.map((item) => `${item.x}:${item.y}`)).size).toBe(
      mockTopology.nodes.length
    );
  });

  it("adds a newly captured site without any static coordinate update", () => {
    const expanded = {
      ...mockTopology,
      nodes: [
        ...mockTopology.nodes,
        {
          ...mockTopology.nodes[0],
          id: "NEW-R1",
          deviceName: "NEW-R1",
          site: "新增站点",
          role: "路由器"
        }
      ]
    };
    const layout = buildTopologyLayout(expanded);
    expect(layout.sites.map((site) => site.site)).toContain("新增站点");
    expect(layout.nodes).toHaveLength(10);
  });
});
