// Shared domain contracts used by features, API adapters and tests.
export type Tone =
  | "success"
  | "warning"
  | "danger"
  | "neutral"
  | "accent"
  | "violet";

export type Reachability = "reachable" | "unreachable" | "unknown";

export interface Device {
  id: string;
  name: string;
  role: string;
  site: string;
  vendor: string;
  model: string;
  platform: string;
  managementIp: string;
  reachability: Reachability;
  overallStatus: string;
  collectedAt: string | null;
  runId: string | null;
  facts: DeviceFact[];
  interfaces: string[];
  lifecycleStatus: "active" | "retired";
}

export interface DeviceFact {
  id: string;
  type: "reachability" | "interface" | "route" | "vlan" | "acl" | "neighbor" | "system";
  label: string;
  value: string;
  observedAt: string;
  tone: Tone;
  runId?: string | null;
}

export interface CollectionRun {
  id: string;
  trigger: "startup" | "manual" | "scheduled" | "recheck" | "tool";
  scope: string;
  status: "running" | "success" | "partial" | "failed" | "skipped" | "cancelled";
  deviceSummary: string;
  sshSummary: string;
  connectivitySummary: string;
  startedAt: string;
  publishedAt: string | null;
  durationSeconds: number | null;
}

export interface CollectionSchedule {
  enabled: boolean;
  scheduler_available: boolean;
  frequency_minutes: number;
  active_window: { start: string; end: string };
  scope: string;
  concurrency: number;
  timeout_seconds: number;
  retries: number;
  channels: Record<string, boolean>;
}

export interface TopologyNode {
  id: string;
  deviceName: string;
  role: string;
  site: string;
  managementIp: string;
  reachability: Reachability;
  summary: string;
  x: number;
  y: number;
}

export interface TopologyEdge {
  id: string;
  source: string;
  target: string;
  label: string;
  status: "up" | "down" | "warning" | "unknown";
}

export interface Topology {
  runId: string | null;
  publishedAt: string | null;
  nodes: TopologyNode[];
  edges: TopologyEdge[];
}

export interface OverviewData {
  devices: {
    total: number;
    reachable: number;
    unreachable: number;
    unknown: number;
  };
  latestRun: CollectionRun | null;
  knowledge: KnowledgeSummary;
  attention: Array<{
    id: string;
    deviceName: string;
    title: string;
    detail: string;
    tone: Tone;
  }>;
  recentSessions: DiagnosisSession[];
  recentRuns: CollectionRun[];
  topology: Topology;
}

export interface KnowledgeSummary {
  status: "ready" | "missing" | "empty" | "degraded" | "error";
  indexVersionId: string | null;
  articleCount: number;
  sourceCount: number;
  chunkCount: number;
  pendingCount: number;
  modelName: string;
  modelDimensions: number | null;
  checkedAt: string;
}

export interface KnowledgeArticle {
  id: string;
  title: string;
  category: string;
  applicableDevice: string;
  version: string;
  source: string;
  contentHash: string;
  hitCount: number;
  lastHitAt: string | null;
  summary: string;
  body: string;
  status: "active" | "draft" | "retired";
}

export interface DiagnosisMessage {
  id: string;
  role: "user" | "assistant" | "tool" | "system";
  createdAt: string;
  content: string;
}

export interface DiagnosisSession {
  id: string;
  title: string;
  question: string;
  status: "active" | "completed" | "failed" | "archived";
  modelId: string;
  createdAt: string;
  updatedAt: string;
  evidence: Array<{
    id: string;
    deviceName: string;
    label: string;
    value: string;
    collectedAt: string;
    runId: string;
    tone: Tone;
  }>;
  knowledge: Array<{
    id: string;
    title: string;
    version: string;
    source: string;
    distance: number | null;
  }>;
  toolCalls: Array<{
    id: string;
    name: string;
    deviceName: string | null;
    target: string;
    status: "success" | "failed";
  }>;
  conclusion: string;
  causes: Array<{ title: string; basis: string; tone: Tone }>;
  suggestion: {
    title: string;
    actions: string[];
    command: string[];
    targetDevice: string | null;
    canExecute: boolean;
    note: string;
    requiresManualConfirmation: boolean;
  } | null;
  missingInformation: string;
  messages: DiagnosisMessage[];
}

export interface PageResult<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
}

export interface DeviceQuery {
  page: number;
  pageSize: number;
  q?: string;
  site?: string;
  status?: Reachability;
}

export interface ArticleQuery {
  page: number;
  pageSize: number;
  q?: string;
  category?: string;
}

export interface TerminalEvent {
  id: string;
  sequence: number;
  kind: "system" | "command" | "output" | "error";
  content: string;
  createdAt: string;
}

export interface TerminalSession {
  id: string;
  deviceName: string;
  operator: string;
  status: "open" | "closed" | "failed";
  backupAcknowledgedAt: string;
  startedAt: string;
  endedAt: string | null;
  events: TerminalEvent[];
}

export interface EnvironmentSettings {
  values: Record<string, string>;
  masked_keys: string[];
  env_path: string;
  needs_restart: boolean;
}

export interface BackgroundJob {
  job_id: string;
  job_type: string;
  status: "queued" | "running" | "success" | "partial" | "failed" | "cancelled";
  progress: number;
  message: string | null;
  result: unknown;
  error_code: string | null;
  created_at_utc: string;
  started_at_utc: string | null;
  finished_at_utc: string | null;
}

export interface DiagnosisApiResult {
  diagnosis_id: number;
  session_id: string;
  conclusion_id: string;
  question: string;
  model_id: string;
  use_kb: boolean;
  evidence: Array<Record<string, unknown>>;
  retrieved_docs: Array<Record<string, unknown>>;
  knowledge_base: { status: string; error: string | null };
  answer: {
    fault_phenomenon?: string;
    evidence_used?: string[];
    possible_causes?: Array<{ cause?: string; basis?: string; confidence?: string }>;
    check_steps?: string[];
    fix_suggestions?: string[];
    recheck_method?: string;
    missing_information?: string;
  };
  response_time_ms: number;
  error: string | null;
}
