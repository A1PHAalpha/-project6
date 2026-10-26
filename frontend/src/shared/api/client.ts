// Console API boundary: mock adapters and the versioned server API live here.
import { z } from "zod";
import {
  mockArticles,
  mockDevices,
  mockKnowledge,
  mockOverview,
  mockRuns,
  mockSession,
  mockTopology
} from "../mocks/data";
import type {
  ArticleQuery,
  Device,
  DeviceQuery,
  BackgroundJob,
  CollectionSchedule,
  DiagnosisApiResult,
  DiagnosisSession,
  EnvironmentSettings,
  KnowledgeArticle,
  KnowledgeSummary,
  OverviewData,
  PageResult,
  TerminalSession,
  Topology,
  WriteConfirmation
} from "../types";

export const mockMode =
  import.meta.env.VITE_USE_MOCK_API === "true" ||
  (import.meta.env.DEV && import.meta.env.VITE_USE_MOCK_API !== "false");

const toneSchema = z.enum(["success", "warning", "danger", "neutral", "accent", "violet"]);
const overviewSchema = z.object({
  devices: z.object({
    total: z.number(),
    reachable: z.number(),
    unreachable: z.number(),
    unknown: z.number()
  }),
  latestRun: z.unknown().nullable(),
  knowledge: z.unknown(),
  attention: z.array(z.unknown()),
  recentSessions: z.array(z.unknown()),
  recentRuns: z.array(z.unknown()),
  topology: z.object({
    runId: z.string().nullable(),
    publishedAt: z.string().nullable(),
    nodes: z.array(z.object({ id: z.string(), deviceName: z.string() }).passthrough()),
    edges: z.array(z.object({ id: z.string(), source: z.string(), target: z.string() }).passthrough())
  })
});

const deviceSchema = z
  .object({
    id: z.string(),
    name: z.string(),
    role: z.string(),
    site: z.string(),
    vendor: z.string(),
    model: z.string(),
    platform: z.string(),
    managementIp: z.string(),
    reachability: z.enum(["reachable", "unreachable", "unknown"]),
    overallStatus: z.string(),
    collectedAt: z.string().nullable(),
    runId: z.string().nullable(),
    facts: z.array(
      z
        .object({
          id: z.string(),
          type: z.enum(["reachability", "interface", "route", "vlan", "acl", "neighbor", "system"]),
          label: z.string(),
          value: z.string(),
          observedAt: z.string(),
          tone: toneSchema
        })
        .passthrough()
    ),
    interfaces: z.array(z.string()),
    lifecycleStatus: z.enum(["active", "retired"])
  })
  .passthrough();

const articleSchema = z
  .object({
    id: z.string(),
    title: z.string(),
    category: z.string(),
    applicableDevice: z.string(),
    version: z.string(),
    source: z.string(),
    contentHash: z.string(),
    hitCount: z.number(),
    lastHitAt: z.string().nullable(),
    summary: z.string(),
    body: z.string(),
    status: z.enum(["active", "draft", "retired"])
  })
  .passthrough();

class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public code?: string
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers
    }
  });
  if (response.status === 401) {
    window.location.assign("/login");
    throw new ApiError("登录状态已失效", 401, "unauthorized");
  }
  if (!response.ok) {
    let detail: { message?: string; code?: string } = {};
    try {
      detail = await response.json();
    } catch {
      // Preserve the HTTP status when the server does not return JSON.
    }
    throw new ApiError(detail.message ?? `请求失败 (${response.status})`, response.status, detail.code);
  }
  return response.json() as Promise<T>;
}

function delay<T>(value: T, ms = 180): Promise<T> {
  return new Promise((resolve) => window.setTimeout(() => resolve(value), ms));
}

function parsePageSize(value: string | null, fallback: number) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? Math.min(parsed, 100) : fallback;
}

export const api = {
  mockMode,

  async getOverview(): Promise<OverviewData> {
    if (mockMode) {
      return delay(mockOverview);
    }
    const raw = await request<unknown>("/api/v1/overview");
    overviewSchema.parse(raw);
    return raw as OverviewData;
  },

  async listDevices(query: DeviceQuery): Promise<PageResult<Device>> {
    if (mockMode) {
      const keyword = query.q?.trim().toLowerCase();
      const filtered = mockDevices.filter((device) => {
        const matchesKeyword =
          !keyword ||
          [device.name, device.managementIp, device.model, device.role]
            .join(" ")
            .toLowerCase()
            .includes(keyword);
        return (
          matchesKeyword &&
          (!query.site || device.site === query.site) &&
          (!query.status || device.reachability === query.status)
        );
      });
      const start = (query.page - 1) * query.pageSize;
      return delay({
        items: filtered.slice(start, start + query.pageSize),
        total: filtered.length,
        page: query.page,
        pageSize: query.pageSize
      });
    }
    const params = new URLSearchParams({
      page: String(query.page),
      page_size: String(query.pageSize)
    });
    if (query.q) params.set("q", query.q);
    if (query.site) params.set("site", query.site);
    if (query.status) params.set("status", query.status);
    const raw = await request<unknown>(`/api/v1/devices?${params}`);
    const page = z
      .object({
        items: z.array(deviceSchema),
        total: z.number(),
        page: z.number(),
        page_size: z.number()
      })
      .parse(raw);
    return {
      items: page.items as Device[],
      total: page.total,
      page: page.page,
      pageSize: page.page_size
    };
  },

  async createDevice(input: {
    name: string;
    role: string;
    site: string;
    vendor: string;
    model: string;
    deviceType: string;
    managementIp: string;
    sshPort: number;
    snmpPort: number | null;
    interfaces: string[];
  }): Promise<Device> {
    if (mockMode) {
      const created: Device = {
        id: `dev_${input.name.toLowerCase().replace(/[^a-z0-9]+/g, "_")}`,
        name: input.name,
        role: input.role,
        site: input.site,
        vendor: input.vendor,
        model: input.model,
        platform: `${input.vendor} ${input.model}`.trim(),
        managementIp: input.managementIp || "未配置",
        reachability: "unknown",
        overallStatus: "unknown",
        collectedAt: null,
        runId: null,
        facts: [
          {
            id: `new_${Date.now()}`,
            type: "system",
            label: "采集状态",
            value: "未采集 · 请先配置管理通道",
            observedAt: new Date().toISOString(),
            tone: "neutral"
          }
        ],
        interfaces: input.interfaces,
        lifecycleStatus: "active"
      };
      mockDevices.unshift(created);
      return delay(created, 240);
    }
    const raw = await request<unknown>("/api/v1/devices", {
      method: "POST",
      body: JSON.stringify(input)
    });
    return deviceSchema.parse(raw) as Device;
  },

  async archiveDevices(names: string[]): Promise<{ archived: number }> {
    if (mockMode) {
      for (const device of mockDevices) {
        if (names.includes(device.name)) device.lifecycleStatus = "retired";
      }
      const retained = mockDevices.filter((device) => device.lifecycleStatus === "active");
      mockDevices.splice(0, mockDevices.length, ...retained);
      return delay({ archived: names.length }, 240);
    }
    return request("/api/v1/devices/archive", {
      method: "POST",
      body: JSON.stringify({ names, reason: "web_console" })
    });
  },

  async triggerCollection(names?: string[]): Promise<{ jobId: string }> {
    if (mockMode) return delay({ jobId: `job_collect_${Date.now()}` }, 320);
    const raw = await request<{ job_id?: string; jobId?: string }>("/api/v1/collection-runs", {
      method: "POST",
      body: JSON.stringify({ device_names: names, trigger: "manual" })
    });
    return { jobId: raw.jobId ?? raw.job_id ?? "" };
  },

  async listCollectionRuns(page = 1, pageSize = 20): Promise<PageResult<import("../types").CollectionRun>> {
    if (mockMode) return delay({ items: mockRuns, total: mockRuns.length, page, pageSize });
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize)
    });
    const raw = await request<{
      items: import("../types").CollectionRun[];
      total: number;
      page: number;
      page_size: number;
    }>(`/api/v1/collection-runs?${params}`);
    return { items: raw.items, total: raw.total, page: raw.page, pageSize: raw.page_size };
  },

  async getJob(jobId: string): Promise<BackgroundJob> {
    if (mockMode) {
      return delay({
        job_id: jobId,
        job_type: "mock",
        status: "success",
        progress: 100,
        message: "演示任务已完成",
        result: null,
        error_code: null,
        created_at_utc: new Date().toISOString(),
        started_at_utc: new Date().toISOString(),
        finished_at_utc: new Date().toISOString()
      });
    }
    return request(`/api/v1/jobs/${jobId}`);
  },

  async getCollectionSchedule(): Promise<CollectionSchedule> {
    if (mockMode) {
      return delay({
        enabled: false,
        scheduler_available: false,
        frequency_minutes: 15,
        active_window: { start: "00:00", end: "23:59" },
        scope: "all",
        concurrency: 4,
        timeout_seconds: 15,
        retries: 1,
        channels: { ssh: true, snmp: true, connectivity: true }
      });
    }
    return request("/api/v1/collection-schedule");
  },

  async updateCollectionSchedule(value: CollectionSchedule): Promise<CollectionSchedule> {
    if (mockMode) return delay(value, 220);
    return request("/api/v1/collection-schedule", {
      method: "PUT",
      body: JSON.stringify(value)
    });
  },

  async getTopology(): Promise<Topology> {
    if (mockMode) return delay(mockTopology);
    const raw = await request<unknown>("/api/v1/topology");
    return raw as Topology;
  },

  async getSession(sessionId?: string): Promise<DiagnosisSession | null> {
    if (mockMode) {
      return sessionId === mockSession.id ? delay(mockSession) : delay(null);
    }
    if (sessionId) {
      const raw = await request<unknown>(`/api/v1/sessions/${sessionId}`);
      return raw as DiagnosisSession;
    }
    return null;
  },

  async listSessions(limit = 20): Promise<DiagnosisSession[]> {
    if (mockMode) return delay([mockSession]);
    return request(`/api/v1/sessions?limit=${limit}`);
  },

  sessionReportUrl(sessionId: string, format: "md" | "html" = "md"): string {
    const base = `/api/v1/sessions/${encodeURIComponent(sessionId)}/report`;
    return format === "html" ? `${base}?format=html` : base;
  },

  async diagnose(
    question: string,
    sessionId?: string,
    options: { useKb?: boolean } = {}
  ): Promise<DiagnosisApiResult> {
    if (mockMode) {
      return delay({
        diagnosis_id: 128,
        session_id: sessionId ?? mockSession.id,
        conclusion_id: "conclusion_mock",
        question,
        model_id: "qwen-plus",
        use_kb: options.useKb ?? true,
        evidence: [],
        retrieved_docs: [],
        knowledge_base: { status: "ready", error: null },
        answer: {
          fault_phenomenon: "演示模式：已完成证据关联和知识检索。",
          possible_causes: [],
          check_steps: [],
          fix_suggestions: [],
          missing_information: "当前为前端演示数据。"
        },
        response_time_ms: 420,
        error: null
      });
    }
    return request("/api/v1/diagnose", {
      method: "POST",
      body: JSON.stringify({
        question,
        session_id: sessionId,
        use_kb: options.useKb ?? true,
        requested_by: "web"
      })
    });
  },

  async getKnowledgeSummary(): Promise<KnowledgeSummary> {
    if (mockMode) return delay(mockKnowledge);
    const raw = await request<unknown>("/api/v1/knowledge-base/status");
    return raw as KnowledgeSummary;
  },

  async listArticles(query: ArticleQuery): Promise<PageResult<KnowledgeArticle>> {
    if (mockMode) {
      const keyword = query.q?.trim().toLowerCase();
      const filtered = mockArticles.filter((article) => {
        const matchesKeyword =
          !keyword ||
          [article.id, article.title, article.source, article.category]
            .join(" ")
            .toLowerCase()
            .includes(keyword);
        return matchesKeyword && (!query.category || article.category === query.category);
      });
      const start = (query.page - 1) * query.pageSize;
      return delay({
        items: filtered.slice(start, start + query.pageSize),
        total: filtered.length,
        page: query.page,
        pageSize: query.pageSize
      });
    }
    const params = new URLSearchParams({
      page: String(query.page),
      page_size: String(query.pageSize)
    });
    if (query.q) params.set("q", query.q);
    if (query.category) params.set("category", query.category);
    const raw = await request<unknown>(`/api/v1/knowledge-base/articles?${params}`);
    const page = z
      .object({
        items: z.array(articleSchema),
        total: z.number(),
        page: z.number(),
        page_size: z.number()
      })
      .parse(raw);
    return {
      items: page.items as KnowledgeArticle[],
      total: page.total,
      page: page.page,
      pageSize: page.page_size
    };
  },

  async rebuildKnowledge(mode: "incremental" | "full"): Promise<{ jobId: string; progress: number }> {
    if (mockMode) return delay({ jobId: `job_kb_${Date.now()}`, progress: 8 }, 300);
    const raw = await request<{ job_id?: string; jobId?: string; progress?: number }>(
      "/api/v1/knowledge-base/rebuild",
      { method: "POST", body: JSON.stringify({ mode }) }
    );
    return { jobId: raw.jobId ?? raw.job_id ?? "", progress: raw.progress ?? 0 };
  },

  async startTerminal(deviceName: string, backupAcknowledged: boolean): Promise<TerminalSession> {
    if (mockMode) {
      const now = new Date().toISOString();
      return delay({
        id: `terminal_${Date.now()}`,
        deviceName,
        operator: "web",
        status: "open",
        backupStatus: "acknowledged",
        backupAcknowledgedAt: backupAcknowledged ? now : "",
        startedAt: now,
        endedAt: null,
        events: [
          {
            id: "t1",
            sequence: 1,
            kind: "system",
            content: `已连接 ${deviceName}。当前会话已开启操作记录。`,
            createdAt: now
          }
        ]
      });
    }
    return request("/api/v1/terminal/sessions", {
      method: "POST",
      body: JSON.stringify({
        device_name: deviceName,
        backup_acknowledged: backupAcknowledged,
        operator: "web"
      })
    });
  },

  async backupTerminal(sessionId: string): Promise<TerminalSession> {
    if (mockMode) {
      const now = new Date().toISOString();
      return delay({
        id: sessionId,
        deviceName: "mock",
        operator: "web",
        status: "open",
        backupStatus: "completed",
        backupAcknowledgedAt: now,
        startedAt: now,
        endedAt: null,
        events: []
      });
    }
    return request(`/api/v1/terminal/sessions/${sessionId}/backup`, { method: "POST" });
  },

  async requestWriteConfirmation(sessionId: string, command: string): Promise<WriteConfirmation> {
    if (mockMode) {
      return delay({
        confirmation_id: `wc_${Date.now()}`,
        expires_at: Date.now() / 1000 + 120
      });
    }
    return request(`/api/v1/terminal/sessions/${sessionId}/write-intents`, {
      method: "POST",
      body: JSON.stringify({ command })
    });
  },

  async sendTerminalCommand(
    sessionId: string,
    command: string,
    options: {
      source?: "manual" | "quick_action";
      confirmed?: boolean;
      confirmationId?: string;
    } = {}
  ): Promise<TerminalSession["events"]> {
    if (mockMode) {
      const now = new Date().toISOString();
      const isWrite = /configure|shutdown|write memory|no shut|ip route|vlan|acl/i.test(command);
      return delay(
        [
          { id: `tc_${Date.now()}`, sequence: 2, kind: "command" as const, content: command, createdAt: now },
          {
            id: `to_${Date.now()}`,
            sequence: 3,
            kind: isWrite ? ("output" as const) : ("output" as const),
            content: isWrite
              ? `% 示例输出来自 NetOps 前端演示模式。\n命令已记录，等待真实设备适配器返回。`
              : `% 示例输出来自 NetOps 前端演示模式。\n${command} 已记录到终端审计。`,
            createdAt: now
          }
        ],
        240
      );
    }
    const raw = await request<{ events: TerminalSession["events"] }>(
      `/api/v1/terminal/sessions/${sessionId}/commands`,
      {
      method: "POST",
      body: JSON.stringify({
        command,
        source: options.source ?? "manual",
        confirmed: options.confirmed ?? true,
        confirmation_id: options.confirmationId ?? null
      })
      }
    );
    return raw.events;
  },

  async closeTerminal(sessionId: string): Promise<void> {
    if (mockMode) return delay(undefined, 120);
    await request(`/api/v1/terminal/sessions/${sessionId}/close`, { method: "POST" });
  },

  async getEnvironmentSettings(): Promise<EnvironmentSettings> {
    if (mockMode) {
      return delay({
        values: {
          LLM_BASE_URL: "https://dashscope.aliyuncs.com/compatible-mode/v1",
          LLM_MODEL: "qwen-plus",
          LLM_PROVIDER: "OpenAI Compatible",
          LLM_API_KEY: "••••••••••••••••",
          LLM_TEMPERATURE: "0.2",
          LLM_TIMEOUT: "60",
          EMBEDDING_MODEL: "BAAI/bge-small-zh-v1.5",
          EMBEDDING_DIMENSION: "512",
          FASTEMBED_CACHE_PATH: "./models/fastembed",
          MODEL_SOURCE: "HuggingFace Hub",
          HF_HUB_OFFLINE: "0"
        },
        masked_keys: ["LLM_API_KEY"],
        env_path: ".env",
        needs_restart: false
      });
    }
    return request("/api/v1/settings/environment");
  },

  async saveEnvironmentSettings(values: Record<string, string>): Promise<{
    saved: boolean;
    updated_keys: string[];
    needs_restart: boolean;
    settings: EnvironmentSettings;
  }> {
    if (mockMode) {
      return delay({
        saved: true,
        updated_keys: Object.keys(values),
        needs_restart: true,
        settings: await this.getEnvironmentSettings()
      });
    }
    return request("/api/v1/settings/environment", {
      method: "PUT",
      body: JSON.stringify({ values })
    });
  },

  async testSettings(kind: "llm" | "embedding"): Promise<{ status: string; message?: string }> {
    if (mockMode) return delay({ status: "ok" }, 700);
    return request(`/api/v1/settings/${kind}/test`, { method: "POST" });
  }
};

export function getPageSizeFromUrl(): number {
  return parsePageSize(new URLSearchParams(window.location.search).get("page_size"), 8);
}
