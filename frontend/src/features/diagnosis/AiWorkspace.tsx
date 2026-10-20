// Diagnosis workbench conversation and evidence panel.
import {
  BookOpen,
  Bot,
  CheckCircle2,
  ChevronDown,
  Clock3,
  Download,
  FileSearch,
  Globe2,
  GripVertical,
  History,
  LoaderCircle,
  Plus,
  Send,
  ShieldCheck,
  SquareTerminal,
  Wrench,
  X
} from "lucide-react";
import { useEffect, useRef, useState, type CSSProperties, type MouseEvent as ReactMouseEvent } from "react";
import { api } from "../../shared/api/client";
import { BackupReminder, ConfirmDialog, StatusBadge } from "../../shared/components/ui";
import { useWorkspace } from "../../shared/state/workspace";
import type { DiagnosisMessage, DiagnosisSession } from "../../shared/types";

const MIN_PANEL_WIDTH = 340;
const MAX_PANEL_WIDTH = 760;
const MIN_PANEL_HEIGHT = 360;

function MessageList({ messages }: { messages: DiagnosisMessage[] }) {
  return (
    <div className="chat-message-list">
      {messages.map((message) => (
        <div className={`chat-message role-${message.role}`} key={message.id}>
          <div className="chat-avatar">{message.role === "user" ? "我" : <Bot size={15} />}</div>
          <div>
            <p>{message.content}</p>
            <span>{new Date(message.createdAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

export function AiWorkspace({
  session,
  onOpenDevice
}: {
  session: DiagnosisSession | null;
  onOpenDevice: (deviceName: string, anchor?: DOMRect) => void;
}) {
  const collapsed = useWorkspace((state) => state.assistantCollapsed);
  const toggleAssistant = useWorkspace((state) => state.toggleAssistant);
  const sessionPanelOpen = useWorkspace((state) => state.sessionPanelOpen);
  const setSessionPanelOpen = useWorkspace((state) => state.setSessionPanelOpen);
  const contexts = useWorkspace((state) => state.contexts);
  const removeContext = useWorkspace((state) => state.removeContext);
  const queueTerminalCommands = useWorkspace((state) => state.queueTerminalCommands);
  const [messages, setMessages] = useState<DiagnosisMessage[]>(session?.messages ?? []);
  const [localSessionId, setLocalSessionId] = useState<string | undefined>(session?.id);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [quickOpen, setQuickOpen] = useState(false);
  const [quickAcknowledged, setQuickAcknowledged] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(true);
  const [useKnowledgeBase, setUseKnowledgeBase] = useState(true);
  const [useWebSearch, setUseWebSearch] = useState(false);
  const [panelWidth, setPanelWidth] = useState(440);
  const [panelHeight, setPanelHeight] = useState<number | null>(660);
  const [panelPosition, setPanelPosition] = useState<{ x: number; y: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const [resizing, setResizing] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const suggestion = session?.suggestion ?? null;
  const suggestionTarget = session?.evidence[0]?.deviceName ?? "BR-A-R2";

  useEffect(() => {
    setMessages(session?.messages ?? []);
    setLocalSessionId(session?.id);
  }, [session?.id, session?.messages]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    if (!collapsed) return;
    setSessionPanelOpen(false);
  }, [collapsed, setSessionPanelOpen]);

  useEffect(() => {
    if (!panelPosition) return;
    const frame = window.requestAnimationFrame(() => {
      const panel = panelRef.current;
      const stage = panel?.parentElement;
      if (!panel || !stage) return;
      const stageRect = stage.getBoundingClientRect();
      const panelRect = panel.getBoundingClientRect();
      const x = Math.max(8, Math.min(stageRect.width - panelRect.width - 8, panelPosition.x));
      const y = Math.max(8, Math.min(stageRect.height - panelRect.height - 8, panelPosition.y));
      if (Math.abs(x - panelPosition.x) > 1 || Math.abs(y - panelPosition.y) > 1) {
        setPanelPosition({ x, y });
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, [collapsed, panelHeight, panelPosition, panelWidth]);

  function resizePanel(event: ReactMouseEvent, axis: "width" | "height" | "both") {
    const panel = panelRef.current;
    const stage = panel?.parentElement;
    if (!panel || !stage) return;
    event.preventDefault();
    const startX = event.clientX;
    const startY = event.clientY;
    const startWidth = panelWidth;
    const startHeight = panelHeight ?? panel.getBoundingClientRect().height;
    const stageRect = stage.getBoundingClientRect();
    setResizing(true);

    const move = (moveEvent: MouseEvent) => {
      if (axis !== "height") {
        const maxWidth = Math.min(MAX_PANEL_WIDTH, stageRect.width - 36);
        setPanelWidth(Math.min(maxWidth, Math.max(MIN_PANEL_WIDTH, startWidth + startX - moveEvent.clientX)));
      }
      if (axis !== "width") {
        const maxHeight = stageRect.height - 16;
        setPanelHeight(Math.min(maxHeight, Math.max(MIN_PANEL_HEIGHT, startHeight + moveEvent.clientY - startY)));
      }
    };
    const stop = () => {
      setResizing(false);
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", stop);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", stop, { once: true });
  }

  function dragPanel(event: ReactMouseEvent) {
    if ((event.target as HTMLElement).closest("button")) return;
    const panel = panelRef.current;
    const stage = panel?.parentElement;
    if (!panel || !stage) return;
    event.preventDefault();
    const panelRect = panel.getBoundingClientRect();
    const stageRect = stage.getBoundingClientRect();
    const startX = event.clientX;
    const startY = event.clientY;
    const startLeft = panelRect.left - stageRect.left;
    const startTop = panelRect.top - stageRect.top;
    setDragging(true);
    const move = (moveEvent: MouseEvent) => {
      const maxLeft = Math.max(8, stageRect.width - panelRect.width - 8);
      const maxTop = Math.max(8, stageRect.height - panelRect.height - 8);
      setPanelPosition({
        x: Math.max(8, Math.min(maxLeft, startLeft + moveEvent.clientX - startX)),
        y: Math.max(8, Math.min(maxTop, startTop + moveEvent.clientY - startY))
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

  async function submitQuestion() {
    const value = input.trim();
    if (!value || sending) return;
    setMessages((current) => [
      ...current,
      {
        id: `user_${Date.now()}`,
        role: "user",
        createdAt: new Date().toISOString(),
        content: value
      }
    ]);
    setInput("");
    setSending(true);
    try {
      const contextHint = contexts.length
        ? `\n\n当前对话上下文：${contexts.map((context) => `@${context.label}`).join("、")}`
        : "";
      const result = await api.diagnose(`${value}${contextHint}`, localSessionId, {
        useKb: useKnowledgeBase
      });
      setLocalSessionId(result.session_id);
      const causes = (result.answer.possible_causes ?? [])
        .map((cause) => cause.cause)
        .filter(Boolean);
      const steps = (result.answer.check_steps ?? []).slice(0, 4);
      const content = [
        result.answer.fault_phenomenon,
        causes.length ? `可能原因：${causes.join("；")}` : null,
        steps.length ? `建议检查：${steps.join("；")}` : null,
        result.answer.missing_information
      ]
        .filter(Boolean)
        .join("\n");
      setMessages((current) => [
        ...current,
        {
          id: `assistant_${Date.now()}`,
          role: "assistant",
          createdAt: new Date().toISOString(),
          content: content || "诊断已完成，结论和证据已写入当前会话。"
        }
      ]);
    } catch (cause) {
      setMessages((current) => [
        ...current,
        {
          id: `assistant_error_${Date.now()}`,
          role: "assistant",
          createdAt: new Date().toISOString(),
          content: cause instanceof Error ? `诊断请求失败：${cause.message}` : "诊断请求失败"
        }
      ]);
    } finally {
      setSending(false);
    }
  }

  function startNewSession() {
    setMessages([]);
    setLocalSessionId(undefined);
    setSessionPanelOpen(false);
    const url = new URL(window.location.href);
    url.searchParams.delete("session");
    window.history.replaceState({}, "", url);
  }

  if (collapsed) {
    const collapsedStyle: CSSProperties = panelPosition
      ? { left: panelPosition.x, top: panelPosition.y, right: "auto", bottom: "auto" }
      : {};
    return (
      <aside
        className={`ai-workspace collapsed ${dragging ? "dragging" : ""}`}
        style={collapsedStyle}
        ref={panelRef}
        aria-label="AI 运维助手"
      >
        <header className="ai-head" onMouseDown={dragPanel} title="拖动调整位置">
          <div className="ai-title">
            <GripVertical size={15} className="drag-grip" />
            <span className="ai-status-dot" />
            <strong>AI 运维助手</strong>
          </div>
          <button className="icon-button" type="button" onClick={toggleAssistant} aria-label="展开 AI 运维助手">
            <ChevronDown className="rotate-left" size={17} />
          </button>
        </header>
      </aside>
    );
  }

  const panelStyle: CSSProperties = {
    width: panelWidth,
    height: panelHeight === null ? "calc(100% - 16px)" : panelHeight,
    ...(panelPosition ? { left: panelPosition.x, top: panelPosition.y, right: "auto", bottom: "auto" } : {})
  };

  return (
    <>
      <aside
        className={`ai-workspace ${resizing ? "resizing" : ""} ${dragging ? "dragging" : ""}`}
        style={panelStyle}
        ref={panelRef}
        aria-label="AI 运维助手"
      >
        <button
          className="ai-resize-handle ai-resize-left"
          type="button"
          aria-label="调整助手宽度"
          onMouseDown={(event) => resizePanel(event, "width")}
        />
        <button
          className="ai-resize-handle ai-resize-top"
          type="button"
          aria-label="调整助手高度"
          onMouseDown={(event) => resizePanel(event, "height")}
        />
        <button
          className="ai-resize-handle ai-resize-corner"
          type="button"
          aria-label="调整助手大小"
          onMouseDown={(event) => resizePanel(event, "both")}
        />

        <header className="ai-head" onMouseDown={dragPanel} title="拖动调整位置">
          <div className="ai-title">
            <button
              className="icon-button ai-session-button"
              type="button"
              aria-label="查看诊断会话"
              title="诊断会话"
              onClick={() => setSessionPanelOpen(!sessionPanelOpen)}
            >
              <History size={16} />
            </button>
            <GripVertical size={16} className="drag-grip" />
            <span className="ai-status-dot" />
            <strong>AI 运维助手</strong>
          </div>
          <div className="ai-head-actions">
            <span className="tag plain">{session ? "会话已保存" : "新会话"}</span>
            <button className="icon-button" type="button" onClick={toggleAssistant} aria-label="收起 AI 运维助手">
              <ChevronDown className="rotate-right" size={17} />
            </button>
          </div>
        </header>

        {sessionPanelOpen ? (
          <div className="session-panel">
            <header>
              <div>
                <strong>诊断会话</strong>
                <span>{session ? "当前会话" : "尚无已保存会话"}</span>
              </div>
              <button className="icon-button" type="button" aria-label="关闭会话列表" onClick={() => setSessionPanelOpen(false)}>
                <X size={15} />
              </button>
            </header>
            {session ? (
              <button className="session-list-item active" type="button">
                <strong>{session.title}</strong>
                <span>{session.id} · {session.modelId}</span>
                <span>{new Date(session.updatedAt).toLocaleString("zh-CN")}</span>
              </button>
            ) : null}
            <button className="button button-quiet session-new-button" type="button" onClick={startNewSession}>
              <Plus size={15} />
              新建会话
            </button>
          </div>
        ) : null}

        <div className={`ai-scroll ${messages.length || session ? "" : "empty"}`} ref={scrollRef}>
          {session ? (
            <div className="session-strip">
              <span>
                <Clock3 size={13} />
                {new Date(session.createdAt).toLocaleString("zh-CN")}
              </span>
              <a href={`/api/export/${session.id}`} target="_blank" rel="noreferrer">
                <Download size={13} />
                导出报告
              </a>
            </div>
          ) : null}

          <MessageList messages={messages} />

          {session?.toolCalls.length ? (
            <section className="chat-card tool-call-card">
              <div className="chat-card-heading">
                <span className="card-eyebrow tone-accent">
                  <Wrench size={14} />
                  工具调用 · 证据已关联
                </span>
              </div>
              <div className="tool-pills">
                {session.toolCalls.map((call) => (
                  <button
                    className={`tool-pill ${call.status}`}
                    type="button"
                    key={call.id}
                    onClick={(event) => onOpenDevice(call.target.split(" → ")[0], event.currentTarget.getBoundingClientRect())}
                  >
                    <span>{call.status === "success" ? "成功" : "失败"}</span>
                    {call.name} · {call.target}
                  </button>
                ))}
              </div>
            </section>
          ) : null}

          {session?.conclusion ? (
            <section className="chat-card conclusion-card">
              <div className="chat-card-heading">
                <strong>诊断结论</strong>
                <StatusBadge tone="success">证据一致</StatusBadge>
              </div>
              <p>{session.conclusion}</p>
              <small>{session.missingInformation}</small>
            </section>
          ) : null}

          {session?.evidence.length ? (
            <section className="chat-card">
              <button className="chat-card-heading expandable" type="button" onClick={() => setDetailsOpen((open) => !open)}>
                <strong>关键证据</strong>
                <span>{session.evidence.length} 条</span>
                <ChevronDown className={detailsOpen ? "" : "rotate-right"} size={15} />
              </button>
              {detailsOpen ? (
                <div className="chat-evidence-list">
                  {session.evidence.map((evidence) => (
                    <button
                      className={`chat-evidence tone-${evidence.tone}`}
                      type="button"
                      key={evidence.id}
                      onClick={(event) => onOpenDevice(evidence.deviceName, event.currentTarget.getBoundingClientRect())}
                    >
                      <span>
                        <strong>{evidence.deviceName} · {evidence.label}</strong>
                        <small>{new Date(evidence.collectedAt).toLocaleTimeString("zh-CN")}</small>
                      </span>
                      <p>{evidence.value}</p>
                      <em className="mono">{evidence.runId}</em>
                    </button>
                  ))}
                </div>
              ) : null}
            </section>
          ) : null}

          {session?.causes.length ? (
            <section className="chat-card cause-card">
              <div className="chat-card-heading">
                <strong>可能原因</strong>
                <span>按证据优先</span>
              </div>
              {session.causes.map((cause, index) => (
                <div className="cause-row" key={cause.title}>
                  <span>{index + 1}</span>
                  <div>
                    <strong>{cause.title}</strong>
                    <small>{cause.basis}</small>
                  </div>
                  <StatusBadge tone={cause.tone}>{index === 0 ? "优先" : "待确认"}</StatusBadge>
                </div>
              ))}
            </section>
          ) : null}

          {suggestion ? (
            <section className="chat-card suggestion-card">
              <div className="chat-card-heading">
                <strong>建议操作</strong>
                <StatusBadge tone="warning">需人工确认</StatusBadge>
              </div>
              <p>{suggestion.title}</p>
              <pre>{suggestion.command.join("\n")}</pre>
              <small>{suggestion.note}</small>
              <div className="suggestion-actions">
                <button
                  className="button button-primary button-sm"
                  type="button"
                  onClick={() => {
                    setQuickAcknowledged(false);
                    setQuickOpen(true);
                  }}
                >
                  <ShieldCheck size={15} />
                  确认后快速执行
                </button>
                <button
                  className="button button-quiet button-sm"
                  type="button"
                  onClick={() => useWorkspace.getState().openTerminal(suggestionTarget)}
                >
                  <SquareTerminal size={15} />
                  打开终端
                </button>
              </div>
            </section>
          ) : null}

          {session?.knowledge.length ? (
            <section className="chat-card knowledge-card">
              <div className="chat-card-heading">
                <span className="card-eyebrow tone-violet">
                  <BookOpen size={14} />
                  知识依据
                </span>
                <span>{session.knowledge.length} 篇</span>
              </div>
              {session.knowledge.map((article) => (
                <button className="knowledge-row" type="button" key={article.id}>
                  <span>
                    <strong>{article.id} · {article.title}</strong>
                    <small>{article.source} · {article.version}</small>
                  </span>
                  <span className="mono">{article.distance?.toFixed(2) ?? "未记录"}</span>
                </button>
              ))}
            </section>
          ) : null}

          {session?.missingInformation ? (
            <div className="missing-info">
              <FileSearch size={15} />
              <span>{session.missingInformation}</span>
            </div>
          ) : null}
        </div>

        <footer className="ai-composer">
          <div className="composer-input-shell">
            {contexts.length ? (
              <div className="context-inline-tray">
                {contexts.map((context) => (
                  <span className="context-chip" key={context.id} title={context.detail}>
                    @{context.label}
                    <button type="button" aria-label={`移除 @${context.label}`} onClick={() => removeContext(context.id)}>
                      <X size={12} />
                    </button>
                  </span>
                ))}
              </div>
            ) : null}
            <textarea
              value={input}
              rows={2}
              placeholder="描述故障现象，或输入复测 / 查看接口…"
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  void submitQuestion();
                }
              }}
            />
            <button
              className="button button-primary composer-submit"
              type="button"
              onClick={() => void submitQuestion()}
              disabled={sending || !input.trim()}
            >
              {sending ? <LoaderCircle className="spin" size={16} /> : <Send size={16} />}
              发送
            </button>
          </div>
          <div className="composer-settings-row">
            <button
              className={`capability-toggle ${useKnowledgeBase ? "active" : ""}`}
              type="button"
              aria-pressed={useKnowledgeBase}
              onClick={() => setUseKnowledgeBase((value) => !value)}
            >
              <BookOpen size={14} />
              知识库
              <span>{useKnowledgeBase ? "开" : "关"}</span>
            </button>
            <button
              className={`capability-toggle ${useWebSearch ? "active" : ""}`}
              type="button"
              aria-pressed={useWebSearch}
              title="网络搜索请求会随诊断参数发送；当前后端搜索适配器仍需接入"
              onClick={() => setUseWebSearch((value) => !value)}
            >
              <Globe2 size={14} />
              网络搜索
              <span>{useWebSearch ? "开" : "关"}</span>
            </button>
            <span className="composer-safety">终端操作需人工确认并全程记录</span>
          </div>
        </footer>
      </aside>

      <ConfirmDialog
        open={quickOpen}
        onOpenChange={setQuickOpen}
        title="确认快速执行诊断命令"
        description="系统会把下列命令送入受审计终端。执行前请阅读命令说明并确认配置备份。"
        confirmLabel="确认并下发"
        confirmDisabled={!quickAcknowledged}
        onConfirm={() => {
          if (!suggestion) return;
          setQuickOpen(false);
          queueTerminalCommands(suggestionTarget, suggestion.command);
        }}
      >
        <BackupReminder deviceName={suggestionTarget} />
        <div className="quick-command-help">
          <strong>命令将检查什么</strong>
          <ul>
            <li>读取两端接口的物理状态、协议状态和错误计数。</li>
            <li>检查邻接关系，确认路由器与交换机连接关系。</li>
            <li>不修改配置；命令与输出会进入终端审计记录，辅助后续备份判断。</li>
          </ul>
        </div>
        <pre className="command-preview">{suggestion?.command.join("\n")}</pre>
        <label className="check-row">
          <input
            type="checkbox"
            checked={quickAcknowledged}
            onChange={(event) => setQuickAcknowledged(event.target.checked)}
          />
          <span>
            <CheckCircle2 size={15} />
            我已阅读命令说明并确认当前配置备份状态
          </span>
        </label>
      </ConfirmDialog>
    </>
  );
}
