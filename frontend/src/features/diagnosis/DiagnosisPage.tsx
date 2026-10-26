import { useQuery } from "@tanstack/react-query";
import { Cable, FileDown } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../../shared/api/client";
import { LinkDetailOverlay } from "../../shared/components/LinkDetailOverlay";
import { NetworkTopology } from "../../shared/components/NetworkTopology";
import { EmptyState, LoadingState, PageHeader, StatusBadge } from "../../shared/components/ui";
import { useWorkspace } from "../../shared/state/workspace";
import type { TopologyEdge, TopologyNode } from "../../shared/types";
import { AiWorkspace } from "./AiWorkspace";

export function DiagnosisPage() {
  const [searchParams] = useSearchParams();
  const openDeviceDrawer = useWorkspace((state) => state.openDeviceDrawer);
  const assistantCollapsed = useWorkspace((state) => state.assistantCollapsed);
  const [selectedLink, setSelectedLink] = useState<TopologyEdge | null>(null);
  const overview = useQuery({ queryKey: ["overview"], queryFn: api.getOverview });
  const devices = useQuery({
    queryKey: ["devices", "diagnosis-lookup"],
    queryFn: () => api.listDevices({ page: 1, pageSize: 100 })
  });
  const session = useQuery({
    queryKey: ["diagnosis-session", searchParams.get("session")],
    queryFn: () => api.getSession(searchParams.get("session") ?? undefined)
  });

  function openDeviceFromName(name: string, anchor?: DOMRect) {
    const device = devices.data?.items.find((item) => item.name === name);
    if (device) openDeviceDrawer(device, anchor);
  }

  useEffect(() => {
    const deviceName = searchParams.get("device");
    if (deviceName) openDeviceFromName(deviceName);
    // The lookup only needs to run again when the URL or device data changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, devices.data]);

  const topology = overview.data?.topology;
  const source = selectedLink ? topology?.nodes.find((node) => node.id === selectedLink.source) : undefined;
  const target = selectedLink ? topology?.nodes.find((node) => node.id === selectedLink.target) : undefined;

  return (
    <div className="diagnosis-page">
      <PageHeader
        title="诊断中心"
        description="全屏拓扑 + AI 工作区。证据、知识来源和设备事实保持可追溯；终端操作必须人工确认并全程记录。"
        actions={
          <>
            <a
              className="button button-quiet"
              href={session.data ? api.sessionReportUrl(session.data.id) : undefined}
              target="_blank"
              rel="noreferrer"
              aria-disabled={!session.data}
            >
              <FileDown size={15} />
              导出报告
            </a>
            <a
              className="button button-quiet"
              href={
                session.data
                  ? api.sessionReportUrl(session.data.id, "html")
                  : undefined
              }
              target="_blank"
              rel="noreferrer"
              aria-disabled={!session.data}
            >
              <FileDown size={15} />
              导出 HTML
            </a>
          </>
        }
      />

      {overview.isLoading || session.isLoading ? (
        <LoadingState label="正在恢复诊断会话" />
      ) : overview.isError || !topology ? (
        <EmptyState title="诊断工作区加载失败" description="无法读取拓扑或当前会话，请检查 API 服务状态。" />
      ) : (
        <section className={`diagnosis-stage ${assistantCollapsed ? "assistant-collapsed" : ""}`}>
          <div className="diagnosis-topology">
            <div className="diagnosis-toolbar">
              <div className="topology-legend">
                <span className="text-success"><i className="status-dot" />可达</span>
                <span className="text-warning"><i className="status-dot" />接口异常</span>
                <span className="text-danger"><i className="status-dot" />不可达</span>
                <span className="text-neutral"><i className="status-dot" />未采集</span>
              </div>
              <span>点击节点或链路查看详情；设备详情可加入对话上下文</span>
            </div>
            <NetworkTopology
              topology={topology}
              mode="interactive"
              reservedRight={assistantCollapsed ? 0 : 456}
              onDeviceClick={(node: TopologyNode, event) => {
                const target = event?.currentTarget;
                openDeviceFromName(
                  node.deviceName,
                  target instanceof HTMLElement ? target.getBoundingClientRect() : undefined
                );
              }}
              onEdgeClick={(edgeId) => setSelectedLink(topology.edges.find((edge) => edge.id === edgeId) ?? null)}
            />
            <div className="topology-link-hints">
              {topology.edges.map((edge) => (
                <button className={`link-hint edge-${edge.status}`} type="button" key={edge.id} onClick={() => setSelectedLink(edge)}>
                  <Cable size={12} />
                  {edge.source} ↔ {edge.target}
                  <StatusBadge tone={edge.status === "up" ? "success" : "danger"} dot={false}>
                    {edge.status}
                  </StatusBadge>
                </button>
              ))}
            </div>
          </div>

          <AiWorkspace session={session.data ?? null} onOpenDevice={openDeviceFromName} />

          {selectedLink && source && target ? (
            <LinkDetailOverlay edge={selectedLink} source={source} target={target} onClose={() => setSelectedLink(null)} />
          ) : null}
        </section>
      )}
    </div>
  );
}
