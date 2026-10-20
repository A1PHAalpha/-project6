// Knowledge catalog and index management.
import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  BookOpen,
  CheckCircle2,
  ChevronRight,
  DatabaseZap,
  LoaderCircle,
  RefreshCw,
  Search,
  X
} from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../shared/api/client";
import {
  EmptyState,
  KpiCard,
  LoadingState,
  PageHeader,
  Pagination,
  Panel,
  StatusBadge
} from "../../shared/components/ui";
import type { KnowledgeArticle } from "../../shared/types";

export function KnowledgePage() {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(5);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [rebuildOpen, setRebuildOpen] = useState(false);
  const [rebuildMode, setRebuildMode] = useState<"incremental" | "full">("incremental");
  const [progress, setProgress] = useState<number | null>(null);
  const [rebuildMessage, setRebuildMessage] = useState<string | null>(null);

  const summary = useQuery({ queryKey: ["knowledge-summary"], queryFn: api.getKnowledgeSummary });
  const articles = useQuery({
    queryKey: ["articles", page, pageSize, query, category],
    queryFn: () => api.listArticles({ page, pageSize, q: query, category: category || undefined })
  });

  const selected =
    articles.data?.items.find((article) => article.id === selectedId) ?? articles.data?.items[0] ?? null;

  useEffect(() => {
    if (selected && selected.id !== selectedId) setSelectedId(selected.id);
  }, [selected, selectedId]);

  const rebuild = useMutation({
    mutationFn: () => api.rebuildKnowledge(rebuildMode),
    onSuccess: async (result) => {
      setProgress(result.progress);
      setRebuildMessage("后台构建任务已创建，旧索引会在新索引发布前保持可用。");
      const poll = async () => {
        try {
          const job = await api.getJob(result.jobId);
          setProgress(job.progress);
          setRebuildMessage(job.message ?? "正在构建知识索引");
          if (["success", "partial", "failed", "cancelled"].includes(job.status)) {
            if (job.status === "success") {
              setRebuildMessage("新索引已发布，旧索引作为历史版本保留。");
              await summary.refetch();
              await articles.refetch();
            }
            return;
          }
        } catch {
          setRebuildMessage("暂时无法读取任务进度，将继续重试。");
        }
        window.setTimeout(poll, 900);
      };
      window.setTimeout(poll, 500);
    }
  });

  if (summary.isLoading) return <LoadingState label="正在检查知识索引" />;
  if (summary.isError || !summary.data) {
    return <EmptyState title="知识库状态加载失败" description="诊断仍可降级为仅使用设备证据。" />;
  }

  const data = summary.data;
  return (
    <div className="page-stack">
      <PageHeader
        title="运维知识库"
        description="知识引用必须能回到文章 ID、来源和版本；索引重建不会覆盖旧版本。"
        actions={
          <>
            <button className="button button-quiet" type="button" onClick={() => void summary.refetch()}>
              <RefreshCw size={15} />
              刷新状态
            </button>
            <button
              className={`button ${data.pendingCount ? "button-warning" : "button-primary"}`}
              type="button"
              onClick={() => setRebuildOpen(true)}
            >
              <DatabaseZap size={15} />
              {data.pendingCount ? `${data.pendingCount} 项待构建` : "重构知识库"}
            </button>
          </>
        }
      />

      <section className="kpi-grid">
        <KpiCard
          label="索引状态"
          value={data.status === "ready" ? "Ready" : data.status}
          meta={<span className="mono">{data.indexVersionId ?? "尚无已发布索引"}</span>}
          tone={data.status === "ready" ? "success" : "warning"}
        />
        <KpiCard
          label="文章"
          value={data.articleCount}
          suffix="篇"
          meta={`${data.sourceCount} 个来源 · ${data.chunkCount} 个检索片段`}
          tone="accent"
        />
        <KpiCard
          label="Embedding"
          value={data.modelName}
          meta={`${data.modelDimensions ?? "未知"} 维 · fastembed-onnx`}
          tone="violet"
        />
        <KpiCard
          label="待构建"
          value={data.pendingCount}
          suffix="项"
          meta="资料有新版本时按钮会高亮"
          tone={data.pendingCount ? "warning" : "success"}
        />
      </section>

      <section className="knowledge-layout">
        <Panel className="knowledge-list-panel">
          <div className="table-toolbar">
            <label className="search-field">
              <Search size={15} />
              <input
                value={query}
                placeholder="搜索标题 / ID / 来源…"
                onChange={(event) => {
                  setQuery(event.target.value);
                  setPage(1);
                }}
              />
            </label>
            <div className="category-pills">
              {["", "接口", "VLAN", "路由", "ACL"].map((item) => (
                <button
                  key={item || "all"}
                  type="button"
                  className={item === category ? "active" : ""}
                  onClick={() => {
                    setCategory(item);
                    setPage(1);
                  }}
                >
                  {item || "全部"}
                </button>
              ))}
            </div>
            <span className="toolbar-count">{articles.data?.total ?? 0} 篇文章</span>
          </div>

          {articles.isLoading ? (
            <LoadingState label="正在读取知识文章" />
          ) : articles.data?.items.length ? (
            <>
              <div className="table-scroll">
                <table className="data-table article-table">
                  <thead>
                    <tr>
                      <th>文章</th>
                      <th>分类</th>
                      <th>适用设备</th>
                      <th>版本</th>
                      <th>来源</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {articles.data.items.map((article) => (
                      <tr
                        key={article.id}
                        className={`clickable-row ${selected?.id === article.id ? "selected-row" : ""}`}
                        onClick={() => setSelectedId(article.id)}
                      >
                        <td>
                          <span className="mono cell-secondary">{article.id}</span>
                          <span className="cell-primary">{article.title}</span>
                        </td>
                        <td><span className="tag tone-accent">{article.category}</span></td>
                        <td>{article.applicableDevice}</td>
                        <td>{article.version}</td>
                        <td>{article.source}</td>
                        <td><ChevronRight size={14} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pagination
                page={page}
                pageSize={pageSize}
                total={articles.data.total}
                onPageChange={setPage}
                onPageSizeChange={(size) => {
                  setPageSize(size);
                  setPage(1);
                }}
              />
            </>
          ) : (
            <EmptyState
              title="没有匹配的文章"
              description="调整搜索条件，或确认知识目录是否已同步。"
              icon={<BookOpen size={22} />}
            />
          )}
        </Panel>

        <ArticlePreview article={selected} />
      </section>

      <Dialog.Root open={rebuildOpen} onOpenChange={setRebuildOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="dialog-content">
            <Dialog.Title className="dialog-title">重构知识库</Dialog.Title>
            <Dialog.Description className="dialog-description">
              构建期间旧索引保持可用。完成后发布新版本，不覆盖历史索引。
            </Dialog.Description>
            <div className="rebuild-options">
              <button
                className={rebuildMode === "incremental" ? "active" : ""}
                type="button"
                onClick={() => setRebuildMode("incremental")}
              >
                <strong>增量构建</strong>
                <span>检测到 {data.pendingCount} 项资料变更，只重建受影响文章。</span>
              </button>
              <button
                className={rebuildMode === "full" ? "active" : ""}
                type="button"
                onClick={() => setRebuildMode("full")}
              >
                <strong>全量重建</strong>
                <span>适合更换 Embedding 模型或索引结构变化；耗时较长。</span>
              </button>
            </div>
            {progress !== null ? (
              <div className="build-progress">
                <div>
                  <span>构建进度</span>
                  <strong>{progress}%</strong>
                </div>
                <div className="progress-track"><span style={{ width: `${progress}%` }} /></div>
                <p><CheckCircle2 size={14} />{rebuildMessage}</p>
              </div>
            ) : null}
            <div className="dialog-actions">
              <Dialog.Close asChild>
                <button className="button button-quiet" type="button">稍后处理</button>
              </Dialog.Close>
              <button
                className="button button-primary"
                type="button"
                disabled={rebuild.isPending || (progress !== null && progress < 100)}
                onClick={() => {
                  if (progress !== null) return;
                  rebuild.mutate();
                }}
              >
                {rebuild.isPending ? <LoaderCircle className="spin" size={15} /> : <DatabaseZap size={15} />}
                开始构建
              </button>
            </div>
            <Dialog.Close asChild>
              <button className="icon-button dialog-close" aria-label="关闭" type="button"><X size={16} /></button>
            </Dialog.Close>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}

function ArticlePreview({ article }: { article: KnowledgeArticle | null }) {
  return (
    <Panel
      className="article-preview-panel"
      title={article?.id ?? "文章预览"}
      action={article ? <StatusBadge tone="success">{article.status}</StatusBadge> : null}
    >
      {article ? (
        <div className="article-preview">
          <h3>{article.title}</h3>
          <div className="tag-row">
            <span className="tag tone-accent">{article.category}</span>
            <span className="tag">{article.applicableDevice}</span>
            <span className="tag plain">{article.version}</span>
          </div>
          <dl>
            <div><dt>来源</dt><dd>{article.source}</dd></div>
            <div><dt>内容哈希</dt><dd className="mono">{article.contentHash}</dd></div>
            <div><dt>命中次数</dt><dd>{article.hitCount} 次诊断</dd></div>
            <div>
              <dt>最近命中</dt>
              <dd>{article.lastHitAt ? new Date(article.lastHitAt).toLocaleString("zh-CN") : "尚未命中"}</dd>
            </div>
          </dl>
          <div className="article-body">
            <p>{article.summary}</p>
            <pre>{article.body}</pre>
          </div>
          <div className="drawer-note">
            检索结果会保存文章版本、索引版本与距离，便于回看诊断依据。
          </div>
        </div>
      ) : (
        <EmptyState title="选择一篇文章" description="从左侧列表选择文章以查看正文和知识治理信息。" />
      )}
    </Panel>
  );
}
