// Operational overview feature.
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, ChevronRight, Clock3, TriangleAlert } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { api } from "../../shared/api/client";
import { NetworkTopology } from "../../shared/components/NetworkTopology";
import {
  EmptyState,
  KpiCard,
  LoadingState,
  PageHeader,
  Panel,
  StatusBadge
} from "../../shared/components/ui";
import type { CollectionRun, Reachability, Tone } from "../../shared/types";

function runTone(status: CollectionRun["status"]): Tone {
  if (status === "success") return "success";
  if (status === "partial" || status === "running") return "warning";
  if (status === "failed") return "danger";
  return "neutral";
}

function reachabilityTone(value: Reachability): Tone {
  if (value === "reachable") return "success";
  if (value === "unreachable") return "danger";
  return "neutral";
}

export function OverviewPage() {
  const navigate = useNavigate();
  const overview = useQuery({ queryKey: ["overview"], queryFn: api.getOverview });

  if (overview.isLoading) return <LoadingState label="正在汇总网络运行状态" />;
  if (overview.isError || !overview.data) {
    return (
      <EmptyState
        title="总览加载失败"
        description={overview.error instanceof Error ? overview.error.message : "请检查 API 服务状态"}
      />
    );
  }

  const data = overview.data;
  return (
    <div className="page-stack">
      <PageHeader
        title="网络运行总览"
        description="最近一次已发布采集是当前状态的唯一依据；未采集不会被推断为故障。"
        meta={
          data.latestRun ? (
            <>
              <span className="mono">{data.latestRun.id}</span>
              <span>·</span>
              <span>{new Date(data.latestRun.publishedAt ?? data.latestRun.startedAt).toLocaleString("zh-CN")}</span>
            </>
          ) : (
            "尚无已发布采集"
          )
        }
        actions={
          <>
            <button className="button button-quiet" type="button" onClick={() => navigate("/assets?tab=runs")}>
              查看采集
            </button>
            <button className="button button-primary" type="button" onClick={() => navigate("/diagnosis")}>
              开始诊断
            </button>
          </>
        }
      />

      <section className="kpi-grid">
        <KpiCard
          label="设备可达"
          value={`${data.devices.reachable} / ${data.devices.total}`}
          meta={`${data.devices.unreachable} 不可达 · ${data.devices.unknown} 未采集`}
          tone={data.devices.unreachable ? "warning" : "success"}
        />
        <KpiCard
          label="最新采集"
          value={data.latestRun ? (data.latestRun.status === "partial" ? "部分成功" : "成功") : "尚无采集"}
          meta={
            data.latestRun
              ? `${data.latestRun.deviceSummary} · ${data.latestRun.durationSeconds ?? "—"}s`
              : "请先执行采集"
          }
          tone={data.latestRun ? runTone(data.latestRun.status) : "neutral"}
        />
        <KpiCard
          label="知识库"
          value={data.knowledge.status === "ready" ? "Ready" : data.knowledge.status}
          meta={`${data.knowledge.articleCount} 篇 · ${data.knowledge.modelName}`}
          tone={data.knowledge.status === "ready" ? "success" : "warning"}
        />
        <KpiCard
          label="待排查"
          value={data.attention.filter((item) => item.tone === "danger").length}
          meta="分支A / 分支B 终端连通性"
          tone="danger"
        />
      </section>

      <section className="overview-main-grid">
        <Panel
          title="网络拓扑概览"
          action={
            <div className="topology-legend" aria-label="拓扑状态图例">
              <span className="text-success">
                <i className="status-dot" />
                可达
              </span>
              <span className="text-warning">
                <i className="status-dot" />
                接口异常
              </span>
              <span className="text-danger">
                <i className="status-dot" />
                不可达
              </span>
              <span className="text-neutral">
                <i className="status-dot" />
                未采集
              </span>
            </div>
          }
          className="topology-panel"
        >
          <NetworkTopology
            topology={data.topology}
            mode="summary"
            compact
            onSurfaceClick={() => navigate("/diagnosis")}
          />
        </Panel>

        <div className="overview-side">
          <Panel
            title="当前捕获的问题"
            action={<span className="tag danger">{data.attention.length} 项</span>}
            className="attention-panel"
          >
            <div className="attention-list">
              {data.attention.map((item) => (
                <button
                  className={`attention-item tone-${item.tone}`}
                  type="button"
                  key={item.id}
                  onClick={() => navigate(`/diagnosis?device=${encodeURIComponent(item.deviceName)}`)}
                >
                  <span className="attention-icon">
                    <TriangleAlert size={14} />
                  </span>
                  <span>
                    <strong>
                      {item.deviceName} · {item.title}
                    </strong>
                    <small>{item.detail}</small>
                  </span>
                  <ChevronRight size={14} />
                </button>
              ))}
            </div>
          </Panel>

          <Panel
            title="最近诊断会话"
            action={
              <button className="button button-quiet button-xs" type="button" onClick={() => navigate("/diagnosis")}>
                全部
              </button>
            }
            className="sessions-panel"
          >
            {data.recentSessions.length ? (
              <div className="compact-session-list">
                {data.recentSessions.slice(0, 2).map((session) => (
                  <button
                    className="compact-session"
                    type="button"
                    key={session.id}
                    onClick={() => navigate(`/diagnosis?session=${session.id}`)}
                  >
                    <span className="compact-session-title">
                      <Clock3 size={13} />
                      {session.title}
                    </span>
                    <span>
                      <span className="mono">{session.id.slice(-10)}</span> · {session.modelId} ·{" "}
                      {session.status === "active" ? "已完成" : session.status}
                    </span>
                    <ArrowRight size={14} />
                  </button>
                ))}
              </div>
            ) : (
              <EmptyState title="尚无诊断会话" description="从一个现场问题开始，系统会用采集事实构建诊断证据。" />
            )}
          </Panel>
        </div>
      </section>

      <Panel
        title="最近采集批次"
        action={
          <button className="button button-quiet button-xs" type="button" onClick={() => navigate("/assets?tab=runs")}>
            全部
          </button>
        }
        className="table-panel"
      >
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>批次</th>
                <th>触发</th>
                <th>范围</th>
                <th>结果</th>
                <th>耗时</th>
                <th>发布</th>
                <th aria-label="操作" />
              </tr>
            </thead>
            <tbody>
              {data.recentRuns.map((run) => (
                <tr key={run.id}>
                  <td>
                    <span className="cell-primary mono">{run.id}</span>
                    <span className="cell-secondary">
                      {run.trigger === "startup" ? "启动采集" : run.trigger === "manual" ? "手动采集" : run.trigger}
                    </span>
                  </td>
                  <td>
                    <span className="tag plain">{run.trigger}</span>
                  </td>
                  <td>{run.scope}</td>
                  <td>
                    <StatusBadge tone={runTone(run.status)}>{run.deviceSummary}</StatusBadge>
                  </td>
                  <td>{run.durationSeconds ? `${run.durationSeconds}s` : "—"}</td>
                  <td>{run.publishedAt ? new Date(run.publishedAt).toLocaleTimeString("zh-CN") : "未发布"}</td>
                  <td>
                    <button
                      className="button button-quiet button-xs"
                      type="button"
                      onClick={() => navigate(`/assets?tab=runs&run=${run.id}`)}
                    >
                      查看
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
