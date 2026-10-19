"""SQLite 数据层：资产、采集批次、状态事实、诊断会话和人工变更。

一期把“按命令追加日志”升级为：

    collection_runs
      -> device_capture_results
        -> collection_items
          -> state_observations

二期在这一采集事实链上增加：

    agent_sessions / agent_messages / context_snapshots
      -> model_calls / retrieval_hits / diagnosis_conclusions / evidence_links

    change_requests / change_steps / change_events
      -> command_executions / physical_actions / config_backups / rechecks

三期增加：

    knowledge_sources / knowledge_articles / knowledge_article_versions
      -> knowledge_index_versions / kb_chunks -> retrieval_hits

    test_suites / test_cases / fault_injections
      -> experiment_runs -> experiment_results

    users / roles / permissions / retention_policies / maintenance_runs

旧 ``collection_records`` 表会在首次迁移时保留为
``collection_records_legacy``，并创建同名兼容视图，避免现有页面和诊断
代码立即失效。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from project_config import load_project_env

load_project_env()

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = os.getenv("SQLITE_DB_PATH") or str(PROJECT_ROOT / "data" / "ops.db")
SCHEMA_VERSION = 5


BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sites (
    site_id TEXT PRIMARY KEY,
    site_code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL UNIQUE,
    parent_site_id TEXT REFERENCES sites(site_id),
    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    device_id TEXT PRIMARY KEY,
    device_key TEXT NOT NULL UNIQUE,
    device_name TEXT NOT NULL UNIQUE,
    site_id TEXT REFERENCES sites(site_id),
    role TEXT,
    vendor TEXT,
    model TEXT,
    os_family TEXT,
    os_version TEXT,
    mgmt_ip TEXT,
    device_type TEXT,
    lifecycle_status TEXT NOT NULL DEFAULT 'active'
        CHECK (lifecycle_status IN ('active', 'retired', 'unknown')),
    raw_json TEXT,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS device_aliases (
    alias_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    UNIQUE (device_id, alias)
);

CREATE TABLE IF NOT EXISTS device_endpoints (
    endpoint_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    protocol TEXT NOT NULL CHECK (protocol IN ('ssh', 'snmp', 'ping', 'netconf', 'http')),
    host TEXT NOT NULL,
    port INTEGER,
    credential_ref TEXT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    last_verified_at_utc TEXT,
    UNIQUE (device_id, protocol, host, port)
);

CREATE TABLE IF NOT EXISTS interfaces (
    interface_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    if_name TEXT NOT NULL,
    if_index INTEGER,
    media_type TEXT,
    first_seen_at_utc TEXT NOT NULL,
    last_seen_at_utc TEXT NOT NULL,
    UNIQUE (device_id, if_name)
);

CREATE TABLE IF NOT EXISTS credential_refs (
    credential_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    provider TEXT NOT NULL,
    external_ref TEXT NOT NULL,
    scope TEXT,
    rotated_at_utc TEXT,
    status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS inventory_versions (
    version_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    captured_at_utc TEXT NOT NULL,
    imported_by TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS inventory_items (
    item_id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL REFERENCES inventory_versions(version_id) ON DELETE CASCADE,
    device_key TEXT NOT NULL,
    device_id TEXT REFERENCES devices(device_id),
    attributes_json TEXT NOT NULL,
    UNIQUE (version_id, device_key)
);

CREATE TABLE IF NOT EXISTS command_policies (
    policy_id TEXT PRIMARY KEY,
    device_type TEXT NOT NULL,
    operation_key TEXT NOT NULL,
    command_template TEXT NOT NULL,
    read_only INTEGER NOT NULL DEFAULT 1 CHECK (read_only IN (0, 1)),
    timeout_ms INTEGER NOT NULL DEFAULT 15000,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    version TEXT NOT NULL DEFAULT 'phase1',
    UNIQUE (device_type, operation_key, version)
);

CREATE TABLE IF NOT EXISTS raw_blobs (
    blob_id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE,
    media_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    content_text TEXT,
    storage_ref TEXT,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collection_runs (
    run_id TEXT PRIMARY KEY,
    trigger_type TEXT NOT NULL
        CHECK (trigger_type IN (
            'startup', 'manual', 'scheduled', 'llm_tool', 'mcp',
            'recheck', 'synthetic', 'migration'
        )),
    scope_type TEXT NOT NULL
        CHECK (scope_type IN ('batch', 'device', 'tool', 'recheck')),
    scope_json TEXT,
    requested_by TEXT,
    source_ref TEXT,
    inventory_version_id TEXT REFERENCES inventory_versions(version_id),
    tool_version TEXT,
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT,
    published_at_utc TEXT,
    status TEXT NOT NULL
        CHECK (status IN ('running', 'success', 'partial', 'failed', 'skipped', 'cancelled')),
    summary_json TEXT,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS device_capture_results (
    result_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES collection_runs(run_id) ON DELETE CASCADE,
    device_id TEXT NOT NULL REFERENCES devices(device_id),
    result_type TEXT NOT NULL DEFAULT 'full'
        CHECK (result_type IN ('full', 'recheck', 'single')),
    reachability TEXT NOT NULL DEFAULT 'unknown'
        CHECK (reachability IN ('reachable', 'unreachable', 'unknown')),
    overall_status TEXT NOT NULL DEFAULT 'running'
        CHECK (overall_status IN ('running', 'success', 'partial', 'failed', 'skipped')),
    error_code TEXT,
    error_message TEXT,
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT,
    published_at_utc TEXT,
    UNIQUE (run_id, device_id, result_type)
);

CREATE TABLE IF NOT EXISTS collection_items (
    item_id TEXT PRIMARY KEY,
    result_id TEXT NOT NULL REFERENCES device_capture_results(result_id) ON DELETE CASCADE,
    device_id TEXT NOT NULL REFERENCES devices(device_id),
    source TEXT NOT NULL
        CHECK (source IN ('ssh', 'snmp', 'ping', 'api', 'mcp', 'manual', 'legacy')),
    capture_type TEXT NOT NULL,
    command_or_tool TEXT,
    policy_id TEXT REFERENCES command_policies(policy_id),
    collection_status TEXT NOT NULL
        CHECK (collection_status IN ('success', 'failed', 'timeout', 'skipped')),
    observation_status TEXT NOT NULL
        CHECK (observation_status IN ('observed', 'unreachable', 'unknown', 'error')),
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT,
    raw_blob_id TEXT REFERENCES raw_blobs(blob_id),
    parsed_json TEXT,
    content_hash TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS state_observations (
    observation_id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES collection_items(item_id) ON DELETE CASCADE,
    device_id TEXT NOT NULL REFERENCES devices(device_id),
    state_type TEXT NOT NULL
        CHECK (state_type IN (
            'reachability', 'system', 'interface', 'route', 'vlan',
            'acl', 'arp', 'neighbor', 'dhcp', 'config', 'log', 'command_output'
        )),
    entity_type TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    confidence TEXT NOT NULL DEFAULT 'high',
    created_at_utc TEXT NOT NULL,
    UNIQUE (item_id, state_type, entity_type, entity_key)
);

CREATE TABLE IF NOT EXISTS config_snapshots (
    config_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(device_id),
    item_id TEXT REFERENCES collection_items(item_id),
    blob_id TEXT NOT NULL REFERENCES raw_blobs(blob_id),
    sha256 TEXT NOT NULL,
    captured_at_utc TEXT NOT NULL,
    is_running INTEGER NOT NULL DEFAULT 1 CHECK (is_running IN (0, 1)),
    baseline_id TEXT,
    UNIQUE (item_id)
);

CREATE TABLE IF NOT EXISTS topology_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES collection_runs(run_id),
    source TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    confidence TEXT
);

CREATE TABLE IF NOT EXISTS topology_nodes (
    node_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES topology_snapshots(snapshot_id) ON DELETE CASCADE,
    device_id TEXT REFERENCES devices(device_id),
    node_key TEXT NOT NULL,
    label TEXT,
    UNIQUE (snapshot_id, node_key)
);

CREATE TABLE IF NOT EXISTS topology_edges (
    edge_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES topology_snapshots(snapshot_id) ON DELETE CASCADE,
    a_node_id TEXT NOT NULL REFERENCES topology_nodes(node_id),
    b_node_id TEXT NOT NULL REFERENCES topology_nodes(node_id),
    a_interface_id TEXT REFERENCES interfaces(interface_id),
    b_interface_id TEXT REFERENCES interfaces(interface_id),
    link_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'up',
    source_observation_id TEXT REFERENCES state_observations(observation_id),
    confidence TEXT,
    UNIQUE (snapshot_id, a_node_id, b_node_id, a_interface_id, b_interface_id)
);

CREATE TABLE IF NOT EXISTS diagnosis_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    question TEXT NOT NULL,
    model_id TEXT NOT NULL,
    prompt_template_id TEXT NOT NULL,
    llm_params_json TEXT,
    retrieved_doc_ids_json TEXT,
    evidence_json TEXT,
    answer_json TEXT NOT NULL,
    use_kb INTEGER NOT NULL DEFAULT 1,
    response_time_ms INTEGER
);

CREATE TABLE IF NOT EXISTS manual_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    diagnosis_id INTEGER,
    created_at TEXT NOT NULL,
    operator TEXT,
    action_desc TEXT,
    backup_ref TEXT,
    recheck_result TEXT,
    status TEXT,
    FOREIGN KEY (diagnosis_id) REFERENCES diagnosis_records(id)
);

CREATE TABLE IF NOT EXISTS system_status (
    status_key TEXT PRIMARY KEY,
    status_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_devices_site ON devices(site_id);
CREATE INDEX IF NOT EXISTS idx_interfaces_device ON interfaces(device_id, if_name);
CREATE INDEX IF NOT EXISTS idx_inventory_items_version ON inventory_items(version_id);
CREATE INDEX IF NOT EXISTS idx_runs_started ON collection_runs(started_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_runs_published ON collection_runs(published_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_results_device_published
    ON device_capture_results(device_id, published_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_results_run ON device_capture_results(run_id);
CREATE INDEX IF NOT EXISTS idx_items_result ON collection_items(result_id, started_at_utc);
CREATE INDEX IF NOT EXISTS idx_items_device_time
    ON collection_items(device_id, started_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_obs_device_type_time
    ON state_observations(device_id, state_type, observed_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_obs_item ON state_observations(item_id);
CREATE INDEX IF NOT EXISTS idx_topology_edges_snapshot ON topology_edges(snapshot_id);
"""

SCHEMA = BASE_SCHEMA


PHASE2_SCHEMA = """
CREATE TABLE IF NOT EXISTS prompt_versions (
    prompt_version_id TEXT PRIMARY KEY,
    template_id TEXT NOT NULL,
    system_prompt TEXT NOT NULL,
    user_template TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at_utc TEXT NOT NULL,
    UNIQUE (template_id, content_hash)
);

CREATE TABLE IF NOT EXISTS agent_sessions (
    session_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_by TEXT,
    model_id TEXT,
    prompt_version_id TEXT REFERENCES prompt_versions(prompt_version_id),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'completed', 'failed', 'archived')),
    started_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    ended_at_utc TEXT
);

CREATE TABLE IF NOT EXISTS agent_messages (
    message_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES agent_sessions(session_id) ON DELETE CASCADE,
    seq_no INTEGER NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant', 'tool')),
    content_text TEXT,
    content_json TEXT,
    created_at_utc TEXT NOT NULL,
    UNIQUE (session_id, seq_no)
);

CREATE TABLE IF NOT EXISTS context_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES agent_sessions(session_id) ON DELETE CASCADE,
    message_id TEXT REFERENCES agent_messages(message_id),
    prompt_version_id TEXT REFERENCES prompt_versions(prompt_version_id),
    rendered_messages_json TEXT NOT NULL,
    rendered_prompt_redacted TEXT,
    prompt_hash TEXT NOT NULL,
    token_count INTEGER NOT NULL DEFAULT 0,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_registry (
    tool_id TEXT PRIMARY KEY,
    tool_name TEXT NOT NULL UNIQUE,
    transport TEXT NOT NULL CHECK (transport IN ('local', 'http', 'mcp')),
    server_name TEXT,
    description TEXT,
    input_schema_json TEXT NOT NULL,
    read_only INTEGER NOT NULL DEFAULT 1 CHECK (read_only IN (0, 1)),
    requires_approval INTEGER NOT NULL DEFAULT 0 CHECK (requires_approval IN (0, 1)),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    version TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_calls (
    tool_call_id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES agent_sessions(session_id),
    message_id TEXT REFERENCES agent_messages(message_id),
    tool_id TEXT NOT NULL REFERENCES tool_registry(tool_id),
    arguments_json TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('requested', 'running', 'success', 'failed')),
    error_code TEXT,
    error_message TEXT,
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_call_results (
    result_id TEXT PRIMARY KEY,
    tool_call_id TEXT NOT NULL UNIQUE REFERENCES tool_calls(tool_call_id) ON DELETE CASCADE,
    result_kind TEXT NOT NULL,
    collection_run_id TEXT REFERENCES collection_runs(run_id),
    result_json TEXT NOT NULL,
    blob_id TEXT REFERENCES raw_blobs(blob_id),
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_calls (
    call_id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES agent_sessions(session_id),
    message_id TEXT REFERENCES agent_messages(message_id),
    context_snapshot_id TEXT REFERENCES context_snapshots(snapshot_id),
    provider TEXT,
    model TEXT NOT NULL,
    request_id TEXT,
    params_json TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    latency_ms INTEGER,
    status TEXT NOT NULL CHECK (status IN ('success', 'error')),
    error_code TEXT,
    error_message TEXT,
    response_blob_id TEXT REFERENCES raw_blobs(blob_id),
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kb_chunks (
    chunk_id TEXT PRIMARY KEY,
    index_name TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    title TEXT,
    category TEXT,
    applicable_device TEXT,
    version TEXT,
    source TEXT,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    UNIQUE (index_name, doc_id, version)
);

CREATE TABLE IF NOT EXISTS retrieval_queries (
    query_id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES agent_sessions(session_id),
    message_id TEXT REFERENCES agent_messages(message_id),
    query_text TEXT NOT NULL,
    filters_json TEXT,
    top_k INTEGER NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('success', 'empty', 'disabled', 'error')),
    error_message TEXT,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS retrieval_hits (
    hit_id TEXT PRIMARY KEY,
    query_id TEXT NOT NULL REFERENCES retrieval_queries(query_id) ON DELETE CASCADE,
    chunk_id TEXT REFERENCES kb_chunks(chunk_id),
    rank_no INTEGER NOT NULL,
    distance REAL,
    doc_id TEXT NOT NULL,
    title TEXT,
    category TEXT,
    applicable_device TEXT,
    version TEXT,
    source TEXT,
    content TEXT,
    content_hash TEXT,
    created_at_utc TEXT NOT NULL,
    UNIQUE (query_id, rank_no)
);

CREATE TABLE IF NOT EXISTS diagnosis_conclusions (
    conclusion_id TEXT PRIMARY KEY,
    diagnosis_id INTEGER NOT NULL UNIQUE REFERENCES diagnosis_records(id),
    session_id TEXT NOT NULL REFERENCES agent_sessions(session_id),
    final_message_id TEXT REFERENCES agent_messages(message_id),
    fault_phenomenon TEXT,
    root_cause TEXT,
    missing_information TEXT,
    confidence TEXT,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_links (
    link_id TEXT PRIMARY KEY,
    conclusion_id TEXT NOT NULL REFERENCES diagnosis_conclusions(conclusion_id) ON DELETE CASCADE,
    link_type TEXT NOT NULL
        CHECK (link_type IN ('device_evidence', 'knowledge', 'tool_result')),
    observation_id TEXT REFERENCES state_observations(observation_id),
    item_id TEXT REFERENCES collection_items(item_id),
    retrieval_hit_id TEXT REFERENCES retrieval_hits(hit_id),
    tool_call_result_id TEXT REFERENCES tool_call_results(result_id),
    claim TEXT,
    role TEXT NOT NULL DEFAULT 'supporting'
        CHECK (role IN ('supporting', 'conflicting', 'context', 'missing')),
    created_at_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_sessions_updated
    ON agent_sessions(updated_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_agent_messages_session
    ON agent_messages(session_id, seq_no);
CREATE INDEX IF NOT EXISTS idx_context_snapshots_session
    ON context_snapshots(session_id, created_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_tool_calls_session
    ON tool_calls(session_id, started_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_model_calls_session
    ON model_calls(session_id, created_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_retrieval_queries_session
    ON retrieval_queries(session_id, created_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_retrieval_hits_query
    ON retrieval_hits(query_id, rank_no);
CREATE INDEX IF NOT EXISTS idx_evidence_links_conclusion
    ON evidence_links(conclusion_id, link_type);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_doc
    ON kb_chunks(doc_id, version);
"""


PHASE2B_SCHEMA = """
CREATE TABLE IF NOT EXISTS change_requests (
    change_id TEXT PRIMARY KEY,
    conclusion_id TEXT REFERENCES diagnosis_conclusions(conclusion_id),
    diagnosis_id INTEGER REFERENCES diagnosis_records(id),
    session_id TEXT REFERENCES agent_sessions(session_id),
    device_id TEXT REFERENCES devices(device_id),
    device_name TEXT NOT NULL,
    change_type TEXT NOT NULL
        CHECK (change_type IN ('physical', 'software', 'mixed')),
    title TEXT NOT NULL,
    description TEXT,
    risk_level TEXT NOT NULL DEFAULT 'medium'
        CHECK (risk_level IN ('low', 'medium', 'high')),
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN (
            'draft', 'pending_approval', 'approved', 'rejected',
            'executing', 'completed', 'failed', 'rolled_back', 'cancelled'
        )),
    requested_by TEXT NOT NULL,
    approved_by TEXT,
    planned_at_utc TEXT,
    started_at_utc TEXT,
    finished_at_utc TEXT,
    rollback_plan TEXT,
    legacy_action_id INTEGER UNIQUE,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS change_steps (
    step_id TEXT PRIMARY KEY,
    change_id TEXT NOT NULL REFERENCES change_requests(change_id) ON DELETE CASCADE,
    seq_no INTEGER NOT NULL,
    step_type TEXT NOT NULL
        CHECK (step_type IN ('physical', 'software', 'verification')),
    source_type TEXT NOT NULL DEFAULT 'human'
        CHECK (source_type IN ('llm', 'human')),
    title TEXT NOT NULL,
    command_text TEXT,
    expected_result TEXT,
    requires_approval INTEGER NOT NULL DEFAULT 0
        CHECK (requires_approval IN (0, 1)),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'ready', 'running', 'success', 'failed', 'skipped')),
    executed_by TEXT,
    started_at_utc TEXT,
    finished_at_utc TEXT,
    result_summary TEXT,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    UNIQUE (change_id, seq_no)
);

CREATE TABLE IF NOT EXISTS change_approvals (
    approval_id TEXT PRIMARY KEY,
    change_id TEXT NOT NULL REFERENCES change_requests(change_id) ON DELETE CASCADE,
    approver TEXT NOT NULL,
    decision TEXT NOT NULL
        CHECK (decision IN ('approved', 'rejected', 'changes_requested')),
    comment TEXT,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS change_events (
    event_id TEXT PRIMARY KEY,
    change_id TEXT NOT NULL REFERENCES change_requests(change_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT,
    payload_json TEXT,
    payload_hash TEXT,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS command_executions (
    execution_id TEXT PRIMARY KEY,
    step_id TEXT NOT NULL REFERENCES change_steps(step_id) ON DELETE CASCADE,
    device_id TEXT REFERENCES devices(device_id),
    command_text TEXT NOT NULL,
    execution_mode TEXT NOT NULL DEFAULT 'manual'
        CHECK (execution_mode IN ('manual', 'readonly', 'approved_automation')),
    executed_by TEXT NOT NULL,
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT,
    exit_code INTEGER,
    stdout_blob_id TEXT REFERENCES raw_blobs(blob_id),
    stderr_blob_id TEXT REFERENCES raw_blobs(blob_id),
    result_summary TEXT,
    approval_id TEXT REFERENCES change_approvals(approval_id),
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS physical_actions (
    action_id TEXT PRIMARY KEY,
    step_id TEXT NOT NULL REFERENCES change_steps(step_id) ON DELETE CASCADE,
    device_id TEXT REFERENCES devices(device_id),
    action_type TEXT NOT NULL
        CHECK (action_type IN ('cable', 'power', 'console', 'module', 'other')),
    description TEXT NOT NULL,
    performed_by TEXT NOT NULL,
    performed_at_utc TEXT NOT NULL,
    evidence_blob_id TEXT REFERENCES raw_blobs(blob_id),
    result TEXT,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS config_backups (
    backup_id TEXT PRIMARY KEY,
    change_id TEXT NOT NULL REFERENCES change_requests(change_id) ON DELETE CASCADE,
    device_id TEXT REFERENCES devices(device_id),
    config_snapshot_id TEXT REFERENCES config_snapshots(config_id),
    blob_id TEXT REFERENCES raw_blobs(blob_id),
    sha256 TEXT,
    captured_at_utc TEXT NOT NULL,
    backup_type TEXT NOT NULL DEFAULT 'before'
        CHECK (backup_type IN ('before', 'after', 'rollback')),
    verified INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0, 1)),
    storage_ref TEXT,
    restore_ref TEXT,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rechecks (
    recheck_id TEXT PRIMARY KEY,
    change_id TEXT NOT NULL REFERENCES change_requests(change_id) ON DELETE CASCADE,
    collection_run_id TEXT REFERENCES collection_runs(run_id),
    source_device_id TEXT REFERENCES devices(device_id),
    source_device_name TEXT,
    target TEXT NOT NULL,
    check_type TEXT NOT NULL DEFAULT 'ping'
        CHECK (check_type IN ('ping', 'interface', 'route', 'acl', 'application')),
    expected_json TEXT,
    actual_json TEXT,
    pass_fail TEXT NOT NULL
        CHECK (pass_fail IN ('pass', 'fail', 'inconclusive')),
    evidence_observation_id TEXT REFERENCES state_observations(observation_id),
    executed_at_utc TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attachments (
    attachment_id TEXT PRIMARY KEY,
    owner_type TEXT NOT NULL
        CHECK (owner_type IN ('change', 'step', 'physical_action', 'report')),
    owner_id TEXT NOT NULL,
    blob_id TEXT REFERENCES raw_blobs(blob_id),
    mime_type TEXT,
    caption TEXT,
    uploaded_by TEXT,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS health_checks (
    check_id TEXT PRIMARY KEY,
    component TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('ready', 'degraded', 'error', 'unknown')),
    details_json TEXT,
    checked_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id TEXT PRIMARY KEY,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT,
    request_id TEXT,
    details_json TEXT,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    report_id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES agent_sessions(session_id),
    conclusion_id TEXT REFERENCES diagnosis_conclusions(conclusion_id),
    change_id TEXT REFERENCES change_requests(change_id),
    report_type TEXT NOT NULL,
    blob_id TEXT REFERENCES raw_blobs(blob_id),
    sha256 TEXT,
    generated_by TEXT,
    generated_at_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_changes_conclusion
    ON change_requests(conclusion_id, created_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_changes_device_status
    ON change_requests(device_id, status, updated_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_changes_status_updated
    ON change_requests(status, updated_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_change_steps_change
    ON change_steps(change_id, seq_no);
CREATE INDEX IF NOT EXISTS idx_change_approvals_change
    ON change_approvals(change_id, created_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_change_events_change
    ON change_events(change_id, created_at_utc);
CREATE INDEX IF NOT EXISTS idx_command_executions_step
    ON command_executions(step_id, created_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_physical_actions_step
    ON physical_actions(step_id, performed_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_config_backups_change
    ON config_backups(change_id, captured_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_rechecks_change
    ON rechecks(change_id, executed_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_attachments_owner
    ON attachments(owner_type, owner_id);
CREATE INDEX IF NOT EXISTS idx_health_checks_component
    ON health_checks(component, checked_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_created
    ON audit_log(created_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_reports_session
    ON reports(session_id, generated_at_utc DESC);
"""


PHASE3_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_sources (
    source_id TEXT PRIMARY KEY,
    source_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    publisher TEXT,
    source_url TEXT,
    edition TEXT,
    license TEXT,
    retrieved_at_utc TEXT,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_articles (
    article_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    category TEXT,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_article_versions (
    article_version_id TEXT PRIMARY KEY,
    article_id TEXT NOT NULL REFERENCES knowledge_articles(article_id) ON DELETE CASCADE,
    version_label TEXT NOT NULL,
    source_id TEXT REFERENCES knowledge_sources(source_id),
    applicable_device TEXT,
    applicable_platform TEXT,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'retired', 'draft')),
    created_at_utc TEXT NOT NULL,
    UNIQUE (article_id, version_label, content_hash)
);

CREATE TABLE IF NOT EXISTS embedding_models (
    model_id TEXT PRIMARY KEY,
    model_name TEXT NOT NULL,
    dimension INTEGER,
    runtime TEXT NOT NULL,
    cache_ref TEXT,
    created_at_utc TEXT NOT NULL,
    UNIQUE (model_name, runtime)
);

CREATE TABLE IF NOT EXISTS knowledge_index_versions (
    index_version_id TEXT PRIMARY KEY,
    index_name TEXT NOT NULL,
    collection_name TEXT NOT NULL,
    embedding_model_id TEXT REFERENCES embedding_models(model_id),
    chroma_path TEXT,
    article_count INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT,
    status TEXT NOT NULL DEFAULT 'building'
        CHECK (status IN ('building', 'ready', 'failed', 'retired')),
    is_active INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
    error_message TEXT,
    built_at_utc TEXT,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'disabled')),
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS roles (
    role_id TEXT PRIMARY KEY,
    role_name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS permissions (
    permission_id TEXT PRIMARY KEY,
    permission_code TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_roles (
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    role_id TEXT NOT NULL REFERENCES roles(role_id) ON DELETE CASCADE,
    granted_at_utc TEXT NOT NULL,
    PRIMARY KEY (user_id, role_id)
);

CREATE TABLE IF NOT EXISTS role_permissions (
    role_id TEXT NOT NULL REFERENCES roles(role_id) ON DELETE CASCADE,
    permission_id TEXT NOT NULL REFERENCES permissions(permission_id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_id)
);

CREATE TABLE IF NOT EXISTS test_suites (
    suite_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    version TEXT NOT NULL DEFAULT '1',
    created_by TEXT,
    created_at_utc TEXT NOT NULL,
    UNIQUE (name, version)
);

CREATE TABLE IF NOT EXISTS test_cases (
    case_id TEXT PRIMARY KEY,
    suite_id TEXT NOT NULL REFERENCES test_suites(suite_id) ON DELETE CASCADE,
    external_id TEXT NOT NULL,
    category TEXT NOT NULL,
    question TEXT NOT NULL,
    expected_json TEXT NOT NULL,
    fixture_json TEXT,
    expected_notes TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at_utc TEXT NOT NULL,
    UNIQUE (suite_id, external_id)
);

CREATE TABLE IF NOT EXISTS fault_injections (
    injection_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES test_cases(case_id) ON DELETE CASCADE,
    device_id TEXT REFERENCES devices(device_id),
    fault_type TEXT NOT NULL,
    injection_mode TEXT NOT NULL
        CHECK (injection_mode IN ('synthetic', 'live')),
    description TEXT,
    before_blob_id TEXT REFERENCES raw_blobs(blob_id),
    after_blob_id TEXT REFERENCES raw_blobs(blob_id),
    injected_at_utc TEXT,
    injected_by TEXT,
    verified_at_utc TEXT,
    status TEXT NOT NULL DEFAULT 'synthetic'
        CHECK (status IN ('planned', 'injected', 'verified', 'failed', 'synthetic')),
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiment_runs (
    experiment_id TEXT PRIMARY KEY,
    suite_id TEXT NOT NULL REFERENCES test_suites(suite_id),
    model_id TEXT NOT NULL,
    provider TEXT,
    prompt_version_id TEXT REFERENCES prompt_versions(prompt_version_id),
    kb_index_version_id TEXT REFERENCES knowledge_index_versions(index_version_id),
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'completed', 'failed', 'cancelled')),
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT,
    summary_json TEXT,
    created_by TEXT,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiment_results (
    result_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES experiment_runs(experiment_id) ON DELETE CASCADE,
    case_id TEXT NOT NULL REFERENCES test_cases(case_id),
    kb_enabled INTEGER NOT NULL CHECK (kb_enabled IN (0, 1)),
    diagnosis_id INTEGER REFERENCES diagnosis_records(id),
    conclusion_id TEXT REFERENCES diagnosis_conclusions(conclusion_id),
    evidence_run_id TEXT REFERENCES collection_runs(run_id),
    evidence_hash TEXT,
    correctness TEXT NOT NULL
        CHECK (correctness IN ('correct', 'partial', 'wrong', 'error', 'skipped')),
    correctness_score REAL NOT NULL DEFAULT 0,
    executability_score REAL NOT NULL DEFAULT 0,
    llm_response_time_ms INTEGER,
    total_response_time_ms INTEGER,
    error_code TEXT,
    error_message TEXT,
    answer_json TEXT,
    evaluation_json TEXT,
    created_at_utc TEXT NOT NULL,
    UNIQUE (experiment_id, case_id, kb_enabled)
);

CREATE TABLE IF NOT EXISTS retention_policies (
    policy_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    target_table TEXT NOT NULL,
    age_days INTEGER NOT NULL CHECK (age_days >= 0),
    action TEXT NOT NULL DEFAULT 'dry_run'
        CHECK (action IN ('dry_run', 'delete_orphan', 'delete')),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    config_json TEXT,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS maintenance_runs (
    run_id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('running', 'success', 'failed')),
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT,
    summary_json TEXT,
    created_by TEXT,
    created_at_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_knowledge_article_versions
    ON knowledge_article_versions(article_id, created_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_knowledge_index_active
    ON knowledge_index_versions(index_name, is_active, created_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_test_cases_suite
    ON test_cases(suite_id, external_id);
CREATE INDEX IF NOT EXISTS idx_fault_injections_case
    ON fault_injections(case_id, created_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_experiment_runs_suite
    ON experiment_runs(suite_id, started_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_experiment_results_run
    ON experiment_results(experiment_id, case_id, kb_enabled);
CREATE INDEX IF NOT EXISTS idx_experiment_results_correctness
    ON experiment_results(experiment_id, correctness);
CREATE INDEX IF NOT EXISTS idx_audit_log_actor
    ON audit_log(actor, created_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_maintenance_runs_type
    ON maintenance_runs(job_type, started_at_utc DESC);
"""


def now_iso() -> str:
    """所有新表统一使用 UTC ISO8601 时间。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _to_utc_iso(value: str | None) -> str | None:
    if not value:
        return value
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _is_after(left: str | None, right: str | None) -> bool:
    left_utc = _to_utc_iso(left)
    right_utc = _to_utc_iso(right)
    if not left_utc:
        return False
    if not right_utc:
        return True
    try:
        return datetime.fromisoformat(left_utc) > datetime.fromisoformat(right_utc)
    except ValueError:
        return left_utc > right_utc


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _stable_id(prefix: str, key: str) -> str:
    return f"{prefix}_{uuid.uuid5(uuid.NAMESPACE_URL, f'netops:{key}').hex}"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json_value(value: Any) -> Any:
    """兼容旧数据中的 JSON 字符串和双重 JSON 编码。"""
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    try:
        first = json.loads(value)
    except (TypeError, ValueError):
        return value
    if isinstance(first, str):
        try:
            return json.loads(first)
        except (TypeError, ValueError):
            return first
    return first


def _json_text(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


@contextmanager
def get_conn():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _view_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='view' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _legacy_tables(conn: sqlite3.Connection, prefix: str) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE ? ORDER BY name",
        (f"{prefix}%",),
    ).fetchall()
    return [row[0] for row in rows]


def _schema_version(conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, "schema_migrations"):
        return 0
    row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return int(row[0] or 0)


def _ensure_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    if column not in _columns(conn, table):
        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')


def _needs_legacy_migration(conn: sqlite3.Connection) -> bool:
    if _table_exists(conn, "devices") and "device_id" not in _columns(conn, "devices"):
        return True
    return _table_exists(conn, "collection_records")


def _backup_before_migration(conn: sqlite3.Connection) -> str | None:
    if not _needs_legacy_migration(conn):
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = f"{DB_PATH}.pre-phase1-{stamp}.bak"
    if Path(backup_path).exists():
        return backup_path
    dst = sqlite3.connect(backup_path)
    try:
        conn.backup(dst)
    finally:
        dst.close()
    return backup_path


def _backup_before_upgrade(conn: sqlite3.Connection) -> str | None:
    """在结构升级前创建可恢复备份，避免就地迁移失败损坏现有数据。"""
    if _needs_legacy_migration(conn):
        return _backup_before_migration(conn)
    version = _schema_version(conn)
    if version == 0 or version >= SCHEMA_VERSION:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = f"{DB_PATH}.pre-v{SCHEMA_VERSION}-{stamp}.bak"
    if Path(backup_path).exists():
        return backup_path
    dst = sqlite3.connect(backup_path)
    try:
        conn.backup(dst)
    finally:
        dst.close()
    return backup_path


def _rename_legacy_tables(conn: sqlite3.Connection) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for table in ("devices", "collection_records"):
        if not _table_exists(conn, table):
            continue
        legacy_name = f"{table}_legacy"
        if _table_exists(conn, legacy_name):
            legacy_name = f"{table}_legacy_{stamp}"
        conn.execute(f'ALTER TABLE "{table}" RENAME TO "{legacy_name}"')


def _site_code(name: str) -> str:
    known = {"总部": "HQ", "分支A": "BR-A", "分支B": "BR-B"}
    if name in known:
        return known[name]
    return "SITE-" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:8].upper()


def _ensure_site_conn(conn: sqlite3.Connection, name: str | None) -> str:
    site_name = (name or "未分类").strip() or "未分类"
    row = conn.execute("SELECT site_id FROM sites WHERE name=?", (site_name,)).fetchone()
    if row:
        return row[0]
    site_id = _stable_id("site", site_name)
    code = _site_code(site_name)
    if conn.execute("SELECT 1 FROM sites WHERE site_code=?", (code,)).fetchone():
        code = f"{code}-{hashlib.sha1(site_name.encode()).hexdigest()[:4].upper()}"
    conn.execute(
        """INSERT INTO sites
           (site_id, site_code, name, parent_site_id, timezone, created_at_utc)
           VALUES (?, ?, ?, NULL, 'Asia/Shanghai', ?)""",
        (site_id, code, site_name, now_iso()),
    )
    return site_id


def _os_family(device_type: str | None) -> str:
    value = (device_type or "").lower()
    if value.startswith("cisco"):
        return "cisco_ios"
    if value.startswith("huawei"):
        return "huawei_vrp"
    if value == "linux" or value.startswith("linux"):
        return "linux"
    return "unknown"


def _ensure_device_conn(
    conn: sqlite3.Connection,
    device_name: str,
    fallback: dict | None = None,
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM devices WHERE device_name=?", (device_name,)
    ).fetchone()
    if row:
        return row
    fallback = fallback or {}
    site_id = _ensure_site_conn(conn, fallback.get("site") or "未分类")
    device_id = _stable_id("dev", device_name)
    timestamp = now_iso()
    raw = json.dumps(fallback, ensure_ascii=False)
    conn.execute(
        """INSERT INTO devices
           (device_id, device_key, device_name, site_id, role, vendor, model,
            os_family, os_version, mgmt_ip, device_type, lifecycle_status,
            raw_json, created_at_utc, updated_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unknown', ?, ?, ?)""",
        (
            device_id,
            fallback.get("device_key") or device_name,
            device_name,
            site_id,
            fallback.get("role"),
            fallback.get("vendor"),
            fallback.get("model"),
            _os_family(fallback.get("device_type")),
            fallback.get("version") or fallback.get("model"),
            fallback.get("mgmt_ip"),
            fallback.get("device_type") or "unknown",
            raw,
            timestamp,
            timestamp,
        ),
    )
    return conn.execute("SELECT * FROM devices WHERE device_id=?", (device_id,)).fetchone()


def _ensure_interface_conn(
    conn: sqlite3.Connection,
    device_id: str,
    if_name: str,
    if_index: int | None = None,
) -> str | None:
    name = (if_name or "").strip()
    if not name:
        return None
    row = conn.execute(
        "SELECT interface_id FROM interfaces WHERE device_id=? AND if_name=?",
        (device_id, name),
    ).fetchone()
    if row:
        return row[0]
    interface_id = _stable_id("if", f"{device_id}:{name}")
    timestamp = now_iso()
    conn.execute(
        """INSERT INTO interfaces
           (interface_id, device_id, if_name, if_index, media_type,
            first_seen_at_utc, last_seen_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (interface_id, device_id, name, if_index, "unknown", timestamp, timestamp),
    )
    return interface_id


def _store_blob_conn(
    conn: sqlite3.Connection,
    content: Any,
    media_type: str = "text/plain",
) -> str | None:
    if content is None:
        return None
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    digest = _sha256(text)
    blob_id = f"blob_{digest}"
    conn.execute(
        """INSERT OR IGNORE INTO raw_blobs
           (blob_id, sha256, media_type, size_bytes, content_text, storage_ref, created_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (blob_id, digest, media_type, len(text.encode("utf-8")), text, "inline", now_iso()),
    )
    return blob_id


def _insert_device_conn(conn: sqlite3.Connection, device: dict) -> dict:
    device_name = device.get("device_name") or device.get("hostname")
    if not device_name:
        raise ValueError("设备缺少 device_name")
    site_id = _ensure_site_conn(conn, device.get("site"))
    device_key = device.get("device_key") or device_name
    existing = conn.execute(
        "SELECT device_id FROM devices WHERE device_key=?", (device_key,)
    ).fetchone()
    device_id = device.get("device_id") or (
        existing[0] if existing else _stable_id("dev", device_key)
    )
    timestamp = now_iso()
    raw_json = json.dumps(device, ensure_ascii=False)
    conn.execute(
        """INSERT INTO devices
           (device_id, device_key, device_name, site_id, role, vendor, model,
            os_family, os_version, mgmt_ip, device_type, lifecycle_status,
            raw_json, created_at_utc, updated_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(device_key) DO UPDATE SET
             device_name=excluded.device_name,
             site_id=excluded.site_id,
             role=excluded.role,
             vendor=excluded.vendor,
             model=excluded.model,
             os_family=excluded.os_family,
             os_version=excluded.os_version,
             mgmt_ip=excluded.mgmt_ip,
             device_type=excluded.device_type,
             raw_json=excluded.raw_json,
             updated_at_utc=excluded.updated_at_utc""",
        (
            device_id,
            device_key,
            device_name,
            site_id,
            device.get("role"),
            device.get("vendor"),
            device.get("model"),
            _os_family(device.get("device_type")),
            device.get("os_version") or device.get("version"),
            device.get("mgmt_ip"),
            device.get("device_type"),
            device.get("lifecycle_status") or "active",
            raw_json,
            timestamp,
            timestamp,
        ),
    )
    row = conn.execute("SELECT * FROM devices WHERE device_key=?", (device_key,)).fetchone()
    device_id = row["device_id"]
    conn.execute(
        """INSERT OR IGNORE INTO device_aliases
           (alias_id, device_id, alias, source, created_at_utc)
           VALUES (?, ?, ?, 'inventory', ?)""",
        (_stable_id("alias", f"{device_id}:{device_name}"), device_id, device_name, timestamp),
    )
    host = device.get("mgmt_ip")
    if host:
        ssh_port = device.get("ssh_port") or 22
        conn.execute(
            """INSERT OR IGNORE INTO device_endpoints
               (endpoint_id, device_id, protocol, host, port, credential_ref,
                enabled, last_verified_at_utc)
               VALUES (?, ?, 'ssh', ?, ?, NULL, 1, NULL)""",
            (_stable_id("ep", f"{device_id}:ssh:{host}:{ssh_port}"), device_id, host, ssh_port),
        )
        if device.get("snmp_port"):
            conn.execute(
                """INSERT OR IGNORE INTO device_endpoints
                   (endpoint_id, device_id, protocol, host, port, credential_ref,
                    enabled, last_verified_at_utc)
                   VALUES (?, ?, 'snmp', ?, ?, NULL, 1, NULL)""",
                (
                    _stable_id("ep", f"{device_id}:snmp:{host}:{device['snmp_port']}"),
                    device_id,
                    host,
                    device["snmp_port"],
                ),
            )
    for interface in device.get("interfaces", []) or []:
        _ensure_interface_conn(conn, device_id, interface)
    result = dict(row)
    result["site"] = conn.execute(
        "SELECT name FROM sites WHERE site_id=?", (site_id,)
    ).fetchone()[0]
    result["site_id"] = site_id
    for key, value in device.items():
        result.setdefault(key, value)
    return result


def upsert_device(device: dict) -> dict:
    init_db()
    with get_conn() as conn:
        return _insert_device_conn(conn, device)


def _upsert_inventory_version_conn(
    conn: sqlite3.Connection,
    source: str,
    source_hash: str,
    captured_at: str | None = None,
    imported_by: str = "system",
    notes: str | None = None,
) -> str:
    row = conn.execute(
        "SELECT version_id FROM inventory_versions WHERE source_hash=? ORDER BY captured_at_utc DESC LIMIT 1",
        (source_hash,),
    ).fetchone()
    if row:
        return row[0]
    version_id = _new_id("inv")
    conn.execute(
        """INSERT INTO inventory_versions
           (version_id, source, source_hash, captured_at_utc, imported_by, notes)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (version_id, source, source_hash, captured_at or now_iso(), imported_by, notes),
    )
    return version_id


def load_inventory_with_version(json_path: str) -> dict:
    init_db()
    with open(json_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    source_hash = hashlib.sha256(raw).hexdigest()
    devices = data.get("devices", [])
    with get_conn() as conn:
        version_id = _upsert_inventory_version_conn(
            conn, str(json_path), source_hash, imported_by="inventory_loader"
        )
        loaded = []
        for device in devices:
            result = _insert_device_conn(conn, device)
            loaded.append(result)
            conn.execute(
                """INSERT INTO inventory_items
                   (item_id, version_id, device_key, device_id, attributes_json)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(version_id, device_key) DO UPDATE SET
                     device_id=excluded.device_id,
                     attributes_json=excluded.attributes_json""",
                (
                    _stable_id("invitem", f"{version_id}:{result['device_key']}"),
                    version_id,
                    result["device_key"],
                    result["device_id"],
                    json.dumps(device, ensure_ascii=False),
                ),
            )
    return {"inventory_version_id": version_id, "devices": loaded, "source_hash": source_hash}


def load_inventory(json_path: str) -> list[dict]:
    return load_inventory_with_version(json_path)["devices"]


def list_devices() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT d.*, s.name AS site
               FROM devices d
               LEFT JOIN sites s ON s.site_id=d.site_id
               ORDER BY d.device_name"""
        ).fetchall()
    return [dict(row) for row in rows]


def _insert_run_conn(
    conn: sqlite3.Connection,
    run_id: str,
    trigger_type: str,
    scope_type: str,
    requested_by: str | None,
    source_ref: str | None,
    inventory_version_id: str | None,
    tool_version: str | None,
    scope: dict | None = None,
    started_at: str | None = None,
) -> str:
    timestamp = started_at or now_iso()
    conn.execute(
        """INSERT INTO collection_runs
           (run_id, trigger_type, scope_type, scope_json, requested_by, source_ref,
            inventory_version_id, tool_version, started_at_utc, finished_at_utc,
            published_at_utc, status, summary_json, created_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, 'running', NULL, ?)""",
        (
            run_id,
            trigger_type,
            scope_type,
            _json_text(scope),
            requested_by,
            source_ref,
            inventory_version_id,
            tool_version or "phase1",
            timestamp,
            timestamp,
        ),
    )
    return run_id


def start_collection_run(
    trigger_type: str = "manual",
    scope_type: str = "batch",
    requested_by: str | None = "cli",
    source_ref: str | None = None,
    inventory_version_id: str | None = None,
    tool_version: str | None = "phase1",
    scope: dict | None = None,
    started_at: str | None = None,
) -> str:
    init_db()
    run_id = _new_id("run")
    with get_conn() as conn:
        _insert_run_conn(
            conn,
            run_id,
            trigger_type,
            scope_type,
            requested_by,
            source_ref,
            inventory_version_id,
            tool_version,
            scope,
            started_at,
        )
    return run_id


def create_device_capture_result(
    run_id: str,
    device_id: str,
    result_type: str = "full",
    started_at: str | None = None,
) -> str:
    result_id = _new_id("result")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO device_capture_results
               (result_id, run_id, device_id, result_type, reachability,
                overall_status, error_code, error_message, started_at_utc,
                finished_at_utc, published_at_utc)
               VALUES (?, ?, ?, ?, 'unknown', 'running', NULL, NULL, ?, NULL, NULL)""",
            (result_id, run_id, device_id, result_type, started_at or now_iso()),
        )
    return result_id


def _insert_item_conn(
    conn: sqlite3.Connection,
    result_id: str,
    device_id: str,
    source: str,
    capture_type: str,
    command_or_tool: str | None,
    collection_status: str,
    observation_status: str,
    raw_output: Any,
    parsed: Any,
    started_at: str,
    finished_at: str,
    policy_id: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> str:
    from collector.state_parser import observations_from_item

    normalized_parsed = _json_value(parsed)
    blob_id = _store_blob_conn(conn, raw_output, "text/plain")
    content_source = raw_output if raw_output is not None else normalized_parsed
    content_hash = _sha256(
        content_source if isinstance(content_source, str)
        else json.dumps(content_source, ensure_ascii=False, sort_keys=True)
    )
    item_id = _new_id("item")
    conn.execute(
        """INSERT INTO collection_items
           (item_id, result_id, device_id, source, capture_type, command_or_tool,
            policy_id, collection_status, observation_status, started_at_utc,
            finished_at_utc, raw_blob_id, parsed_json, content_hash,
            error_code, error_message, created_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            item_id,
            result_id,
            device_id,
            source,
            capture_type,
            command_or_tool,
            policy_id,
            collection_status,
            observation_status,
            started_at,
            finished_at,
            blob_id,
            _json_text(normalized_parsed),
            content_hash,
            error_code,
            error_message,
            now_iso(),
        ),
    )

    item_for_parser = {
        "command_or_tool": command_or_tool,
        "command": command_or_tool,
        "capture_type": capture_type,
        "raw_output": raw_output,
        "parsed": normalized_parsed,
        "collection_status": collection_status,
        "observation_status": observation_status,
    }
    if collection_status == "success" or (
        collection_status == "failed" and capture_type == "ping"
    ):
        for observation in observations_from_item(item_for_parser):
            _insert_observation_conn(
                conn,
                item_id=item_id,
                device_id=device_id,
                observed_at=finished_at,
                observation=observation,
            )

    if capture_type == "config" and blob_id:
        digest = _sha256(raw_output or "")
        conn.execute(
            """INSERT OR IGNORE INTO config_snapshots
               (config_id, device_id, item_id, blob_id, sha256, captured_at_utc,
                is_running, baseline_id)
               VALUES (?, ?, ?, ?, ?, ?, 1, NULL)""",
            (_stable_id("cfg", item_id), device_id, item_id, blob_id, digest, finished_at),
        )
    return item_id


def _insert_observation_conn(
    conn: sqlite3.Connection,
    item_id: str,
    device_id: str,
    observed_at: str,
    observation: dict,
) -> str:
    payload = observation.get("payload") or {}
    if observation.get("state_type") == "interface":
        _ensure_interface_conn(
            conn,
            device_id,
            observation.get("entity_key") or payload.get("if_name") or "",
            payload.get("if_index"),
        )
    observation_id = _new_id("obs")
    conn.execute(
        """INSERT OR IGNORE INTO state_observations
           (observation_id, item_id, device_id, state_type, entity_type,
            entity_key, observed_at_utc, payload_json, confidence, created_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            observation_id,
            item_id,
            device_id,
            observation["state_type"],
            observation.get("entity_type") or "unknown",
            observation.get("entity_key") or "unknown",
            observed_at,
            json.dumps(payload, ensure_ascii=False),
            observation.get("confidence") or "high",
            now_iso(),
        ),
    )
    row = conn.execute(
        """SELECT observation_id FROM state_observations
           WHERE item_id=? AND state_type=? AND entity_type=? AND entity_key=?""",
        (
            item_id,
            observation["state_type"],
            observation.get("entity_type") or "unknown",
            observation.get("entity_key") or "unknown",
        ),
    ).fetchone()
    return row[0] if row else observation_id


def insert_collection_item(
    result_id: str,
    device_id: str,
    source: str,
    capture_type: str | None = None,
    command_or_tool: str | None = None,
    collection_status: str = "success",
    observation_status: str | None = None,
    raw_output: Any = None,
    parsed: Any = None,
    policy_id: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> str:
    from collector.state_parser import capture_type_for_command

    capture = capture_type or capture_type_for_command(command_or_tool)
    if observation_status is None:
        if collection_status == "success":
            observation_status = "observed"
        elif capture == "ping":
            observation_status = "unreachable"
        else:
            observation_status = "error"
    start = started_at or now_iso()
    finish = finished_at or start
    with get_conn() as conn:
        return _insert_item_conn(
            conn,
            result_id=result_id,
            device_id=device_id,
            source=source,
            capture_type=capture,
            command_or_tool=command_or_tool,
            collection_status=collection_status,
            observation_status=observation_status,
            raw_output=raw_output,
            parsed=parsed,
            started_at=start,
            finished_at=finish,
            policy_id=policy_id,
            error_code=error_code,
            error_message=error_message,
        )


def finalize_device_capture_result(
    result_id: str,
    reachability: str,
    overall_status: str,
    error_code: str | None = None,
    error_message: str | None = None,
    finished_at: str | None = None,
    publish: bool = True,
) -> None:
    finish = finished_at or now_iso()
    with get_conn() as conn:
        conn.execute(
            """UPDATE device_capture_results
               SET reachability=?, overall_status=?, error_code=?,
                   error_message=?, finished_at_utc=?, published_at_utc=?
               WHERE result_id=?""",
            (
                reachability,
                overall_status,
                error_code,
                error_message,
                finish,
                finish if publish else None,
                result_id,
            ),
        )


def finish_collection_run(
    run_id: str,
    status: str,
    summary: dict | None = None,
    publish: bool = True,
    finished_at: str | None = None,
) -> None:
    finish = finished_at or now_iso()
    with get_conn() as conn:
        conn.execute(
            """UPDATE collection_runs
               SET status=?, summary_json=?, finished_at_utc=?, published_at_utc=?
               WHERE run_id=?""",
            (
                status,
                _json_text(summary),
                finish,
                finish if publish else None,
                run_id,
            ),
        )


def _insert_collection_batch_conn(
    conn: sqlite3.Connection,
    records: list[dict],
    trigger_type: str,
    result_type: str,
    requested_by: str,
    inventory_version_id: str | None = None,
    scope_type: str | None = None,
) -> dict:
    run_id = _new_id("run")
    timestamps = [
        _to_utc_iso(r.get("collected_at"))
        for r in records
        if r.get("collected_at")
    ]
    timestamp = min(timestamps) if timestamps else now_iso()
    if scope_type is None:
        scope_type = "batch" if len({r.get("device_name") for r in records}) > 1 else "device"
    _insert_run_conn(
        conn,
        run_id,
        trigger_type,
        scope_type,
        requested_by,
        None,
        inventory_version_id,
        "phase1",
        {"device_names": sorted({r.get("device_name") for r in records if r.get("device_name")})},
        timestamp,
    )
    grouped: dict[str, list[dict]] = {}
    for record in records:
        name = record.get("device_name")
        if not name:
            continue
        grouped.setdefault(name, []).append(record)

    item_ids: list[str] = []
    success_count = 0
    failure_count = 0
    for name, device_records in grouped.items():
        device = _ensure_device_conn(conn, name, device_records[0])
        result_id = _new_id("result")
        started = min(
            (_to_utc_iso(r.get("collected_at")) or timestamp)
            for r in device_records
        )
        conn.execute(
            """INSERT INTO device_capture_results
               (result_id, run_id, device_id, result_type, reachability,
                overall_status, started_at_utc)
               VALUES (?, ?, ?, ?, 'unknown', 'running', ?)""",
            (result_id, run_id, device["device_id"], result_type, started),
        )
        statuses = []
        device_success = 0
        for record in device_records:
            status = "success" if record.get("status") == "success" else "failed"
            command = record.get("command")
            from collector.state_parser import capture_type_for_command

            capture = record.get("capture_type") or capture_type_for_command(command)
            observation_status = record.get("observation_status")
            # 旧数据和人工复测里，ping 返回“不可达”常被记成 failed；
            # 只要拿到了原始输出，就说明采集本身成功，失败的是目标可达性。
            if (
                capture == "ping"
                and status == "failed"
                and record.get("raw_output")
            ):
                status = "success"
                observation_status = "observed"
            if observation_status is None:
                observation_status = (
                    "observed" if status == "success"
                    else "unreachable" if capture == "ping"
                    else "error"
                )
            source = record.get("source") or (
                "snmp" if (command or "").upper().startswith("SNMP:")
                else "ping" if (command or "").lower().startswith("ping")
                else "ssh"
            )
            item_id = _insert_item_conn(
                conn,
                result_id=result_id,
                device_id=device["device_id"],
                source=source,
                capture_type=capture,
                command_or_tool=command,
                collection_status=status,
                observation_status=observation_status,
                raw_output=record.get("raw_output"),
                parsed=record.get("parsed"),
                started_at=_to_utc_iso(record.get("collected_at")) or started,
                finished_at=_to_utc_iso(record.get("collected_at")) or started,
                error_code=record.get("error_code"),
                error_message=record.get("fail_reason"),
            )
            item_ids.append(item_id)
            statuses.append(status)
            if status == "success":
                success_count += 1
                device_success += 1
            else:
                failure_count += 1
        reachable = (
            "reachable" if device_success
            else "unreachable" if statuses
            else "unknown"
        )
        overall = "success" if all(s == "success" for s in statuses) else (
            "partial" if any(s == "success" for s in statuses) else "failed"
        )
        finish = max(
            (_to_utc_iso(r.get("collected_at")) or started)
            for r in device_records
        )
        error_message = next(
            (record.get("fail_reason") for record in device_records if record.get("fail_reason")),
            None,
        )
        conn.execute(
            """UPDATE device_capture_results
               SET reachability=?, overall_status=?, error_code=?, error_message=?,
                   finished_at_utc=?, published_at_utc=?
               WHERE result_id=?""",
            (
                reachable,
                overall,
                "collection_failed" if overall == "failed" else None,
                error_message,
                finish,
                finish,
                result_id,
            ),
        )
    run_status = (
        "success" if failure_count == 0
        else "partial" if success_count else "failed"
    )
    summary = {
        "mode": "batch",
        "device_count": len(grouped),
        "success_items": success_count,
        "failed_items": failure_count,
    }
    finish = max(timestamps) if timestamps else now_iso()
    conn.execute(
        """UPDATE collection_runs
           SET status=?, summary_json=?, finished_at_utc=?, published_at_utc=?
           WHERE run_id=?""",
        (run_status, _json_text(summary), finish, finish, run_id),
    )
    _build_topology_conn(conn, run_id)
    return {"run_id": run_id, "item_ids": item_ids, "summary": summary}


def insert_collection_batch(
    records: Iterable[dict],
    trigger_type: str = "synthetic",
    result_type: str = "full",
    requested_by: str = "legacy_api",
    inventory_version_id: str | None = None,
    scope_type: str | None = None,
) -> dict:
    init_db()
    records = [dict(record) for record in records]
    if not records:
        return {"run_id": None, "item_ids": [], "summary": {}}
    with get_conn() as conn:
        return _insert_collection_batch_conn(
            conn,
            records,
            trigger_type,
            result_type,
            requested_by,
            inventory_version_id,
            scope_type,
        )


def reset_collection_data() -> None:
    """清空采集证据，仅供隔离测试/演示环境使用。

    资产台账、诊断历史和人工处理记录不会被删除。
    """
    init_db()
    with get_conn() as conn:
        conn.execute("DELETE FROM topology_snapshots")
        conn.execute("DELETE FROM config_snapshots")
        conn.execute("DELETE FROM collection_runs")


def insert_collection_record(
    device_name,
    status,
    command=None,
    raw_output=None,
    parsed=None,
    fail_reason=None,
    collected_at=None,
    source=None,
    trigger_type="legacy",
    result_type="full",
):
    """兼容旧调用：一条记录进入独立的采集批次。

    批量场景应优先使用 ``insert_collection_batch``，避免同一设备的多条
    命令被拆成多个批次。
    """
    record = {
        "device_name": device_name,
        "status": status,
        "command": command,
        "raw_output": raw_output,
        "parsed": parsed,
        "fail_reason": fail_reason,
        "collected_at": collected_at,
        "source": source,
    }
    batch = insert_collection_batch(
        [record],
        trigger_type=trigger_type,
        result_type=result_type,
        requested_by="legacy_api",
    )
    return batch["item_ids"][0] if batch["item_ids"] else batch["run_id"]


def _latest_result_conn(
    conn: sqlite3.Connection,
    device_id: str,
    result_types: tuple[str, ...],
) -> sqlite3.Row | None:
    placeholders = ",".join("?" for _ in result_types)
    return conn.execute(
        f"""SELECT dcr.*, r.run_id, r.published_at_utc AS run_published_at_utc
            FROM device_capture_results dcr
            JOIN collection_runs r ON r.run_id=dcr.run_id
            WHERE dcr.device_id=?
              AND dcr.result_type IN ({placeholders})
              AND dcr.published_at_utc IS NOT NULL
              AND r.published_at_utc IS NOT NULL
            ORDER BY julianday(r.published_at_utc) DESC,
                     julianday(dcr.published_at_utc) DESC,
                     dcr.result_id DESC
            LIMIT 1""",
        (device_id, *result_types),
    ).fetchone()


def _result_observations_conn(
    conn: sqlite3.Connection,
    result_id: str,
) -> list[dict]:
    rows = conn.execute(
        """SELECT so.*, ci.command_or_tool, ci.source, ci.capture_type,
                  d.device_name, s.name AS site
           FROM state_observations so
           JOIN collection_items ci ON ci.item_id=so.item_id
           JOIN devices d ON d.device_id=so.device_id
           LEFT JOIN sites s ON s.site_id=d.site_id
           WHERE ci.result_id=?
           ORDER BY so.observed_at_utc DESC, so.rowid DESC""",
        (result_id,),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["payload"] = _json_value(item.pop("payload_json")) or {}
        result.append(item)
    return result


def _dedupe_observations(observations: list[dict]) -> list[dict]:
    seen: dict[tuple, int] = {}
    deduped = []
    for observation in observations:
        key = (
            observation.get("state_type"),
            observation.get("entity_type"),
            observation.get("entity_key"),
        )
        if key in seen:
            existing = deduped[seen[key]]
            existing_payload = existing.setdefault("payload", {})
            for field, value in (observation.get("payload") or {}).items():
                if existing_payload.get(field) is None and value is not None:
                    existing_payload[field] = value
            continue
        seen[key] = len(deduped)
        deduped.append(observation)
    return deduped


def get_current_device_bundle(device_name: str) -> dict:
    with get_conn() as conn:
        device = conn.execute(
            """SELECT d.*, s.name AS site
               FROM devices d LEFT JOIN sites s ON s.site_id=d.site_id
               WHERE d.device_name=?""",
            (device_name,),
        ).fetchone()
        if not device:
            return {"device": None, "result": None, "items": [], "observations": []}
        result = _latest_result_conn(conn, device["device_id"], ("full",))
        if result is None:
            result = _latest_result_conn(conn, device["device_id"], ("single",))
        if result is None:
            result = _latest_result_conn(
                conn, device["device_id"], ("full", "single", "recheck")
            )
        items = []
        observations = []
        if result:
            item_rows = conn.execute(
                """SELECT ci.*, rb.content_text AS raw_output
                   FROM collection_items ci
                   LEFT JOIN raw_blobs rb ON rb.blob_id=ci.raw_blob_id
                   WHERE ci.result_id=?
                   ORDER BY ci.started_at_utc, ci.item_id""",
                (result["result_id"],),
            ).fetchall()
            items = [dict(row) for row in item_rows]
            observations = _result_observations_conn(conn, result["result_id"])

        single = _latest_result_conn(conn, device["device_id"], ("single",))
        if single and (result is None or single["result_id"] != result["result_id"]):
            observations = _result_observations_conn(conn, single["result_id"]) + observations

        recheck = _latest_result_conn(conn, device["device_id"], ("recheck",))
        recheck_newer = bool(
            recheck
            and (result is None or _is_after(
                recheck["published_at_utc"], result["published_at_utc"]
            ))
        )
        if recheck and (result is None or recheck["result_id"] != result["result_id"]):
            recheck_obs = _result_observations_conn(conn, recheck["result_id"])
            observations = recheck_obs + observations
        effective_reachability = result["reachability"] if result else "unknown"
        if recheck_newer and recheck:
            effective_reachability = recheck["reachability"]

        if result and (
            result["reachability"] == "unreachable"
            or (result["reachability"] == "unknown" and result["overall_status"] == "failed")
        ) and result["result_type"] != "recheck" and not (
            recheck_newer and recheck["reachability"] == "reachable"
        ):
            observations = [
                {
                    "observation_id": None,
                    "item_id": None,
                    "device_id": device["device_id"],
                    "device_name": device["device_name"],
                    "site": device["site"],
                    "state_type": "reachability",
                    "entity_type": "device",
                    "entity_key": device["device_name"],
                    "observed_at_utc": result["finished_at_utc"] or result["started_at_utc"],
                    "payload": {
                        "reachable": False,
                        "error": result["error_message"],
                    },
                    "confidence": "high",
                    "source": "collector",
                    "capture_type": "reachability",
                    "command_or_tool": "device_reachability",
                    "run_id": result["run_id"],
                    "result_id": result["result_id"],
                }
            ]
        observations = _dedupe_observations(observations)
        return {
            "device": dict(device),
            "result": dict(result) if result else None,
            "items": items,
            "observations": observations,
            "effective_reachability": effective_reachability,
        }


def get_current_state(device_names: list[str] | None = None) -> list[dict]:
    names = device_names or [d["device_name"] for d in list_devices()]
    state = []
    for name in names:
        bundle = get_current_device_bundle(name)
        state.extend(bundle["observations"])
    return state


def get_current_device_status(device_names: list[str] | None = None) -> list[dict]:
    names = device_names or [d["device_name"] for d in list_devices()]
    statuses = []
    for name in names:
        bundle = get_current_device_bundle(name)
        device = bundle["device"]
        result = bundle["result"]
        statuses.append(
            {
                "device_name": name,
                "device_id": device["device_id"] if device else None,
                "site": device["site"] if device else None,
                "reachability": (
                    bundle.get("effective_reachability")
                    or (result["reachability"] if result else "unknown")
                ),
                "overall_status": result["overall_status"] if result else "no_data",
                "last_collected_at": (
                    result["finished_at_utc"] or result["started_at_utc"]
                ) if result else None,
                "run_id": result["run_id"] if result else None,
                "observation_count": len(bundle["observations"]),
                "error": result["error_message"] if result else None,
            }
        )
    return statuses


def get_all_latest_status() -> list[dict]:
    rows = []
    for status in get_current_device_status():
        bundle = get_current_device_bundle(status["device_name"])
        items = bundle["items"]
        if status["reachability"] != "reachable":
            rows.append(
                {
                    "id": None,
                    "device_name": status["device_name"],
                    "command": "device_reachability",
                    "status": "failed" if status["overall_status"] == "failed" else "unknown",
                    "collected_at": status["last_collected_at"],
                    "fail_reason": status["error"],
                    "source": "collector",
                    "capture_type": "reachability",
                    "run_id": status["run_id"],
                    "result_id": None,
                }
            )
        if items:
            for item in items:
                rows.append(
                    {
                        "id": item["item_id"],
                        "device_name": status["device_name"],
                        "command": item.get("command_or_tool") or item.get("capture_type"),
                        "status": "success" if item.get("collection_status") == "success" else "failed",
                        "collected_at": item.get("finished_at_utc") or item.get("started_at_utc"),
                        "fail_reason": item.get("error_message"),
                        "source": item.get("source"),
                        "capture_type": item.get("capture_type"),
                        "run_id": status["run_id"],
                        "result_id": item.get("result_id"),
                    }
                )
        elif status["reachability"] == "reachable":
            rows.append(
                {
                    "id": None,
                    "device_name": status["device_name"],
                    "command": "device_reachability",
                    "status": "failed" if status["overall_status"] == "failed" else "unknown",
                    "collected_at": status["last_collected_at"],
                    "fail_reason": status["error"],
                    "source": "collector",
                    "capture_type": "reachability",
                    "run_id": status["run_id"],
                    "result_id": None,
                }
            )
    return rows


def get_latest_record(device_name: str, command: str = None):
    with get_conn() as conn:
        if command:
            row = conn.execute(
                """SELECT * FROM collection_records
                   WHERE device_name=? AND command=?
                   ORDER BY julianday(collected_at) DESC LIMIT 1""",
                (device_name, command),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT * FROM collection_records
                   WHERE device_name=?
                   ORDER BY julianday(collected_at) DESC LIMIT 1""",
                (device_name,),
            ).fetchone()
    return dict(row) if row else None


def get_device_collection_history(device_name: str, limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM collection_records
               WHERE device_name=? ORDER BY julianday(collected_at) DESC LIMIT ?""",
            (device_name, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def _resolve_device_conn(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    row = conn.execute("SELECT * FROM devices WHERE device_name=?", (name,)).fetchone()
    if row:
        return row
    return conn.execute(
        """SELECT d.* FROM devices d
           JOIN device_aliases a ON a.device_id=d.device_id
           WHERE a.alias=? LIMIT 1""",
        (name,),
    ).fetchone()


def _node_conn(
    conn: sqlite3.Connection,
    snapshot_id: str,
    node_key: str,
    label: str,
    device_id: str | None,
) -> str:
    row = conn.execute(
        "SELECT node_id FROM topology_nodes WHERE snapshot_id=? AND node_key=?",
        (snapshot_id, node_key),
    ).fetchone()
    if row:
        return row[0]
    node_id = _new_id("node")
    conn.execute(
        """INSERT INTO topology_nodes
           (node_id, snapshot_id, device_id, node_key, label)
           VALUES (?, ?, ?, ?, ?)""",
        (node_id, snapshot_id, device_id, node_key, label),
    )
    return node_id


def _build_topology_conn(conn: sqlite3.Connection, run_id: str) -> str | None:
    rows = conn.execute(
        """SELECT so.*, d.device_name AS local_device
           FROM state_observations so
           JOIN collection_items ci ON ci.item_id=so.item_id
           JOIN device_capture_results dcr ON dcr.result_id=ci.result_id
           JOIN devices d ON d.device_id=so.device_id
           WHERE dcr.run_id=? AND so.state_type='neighbor'
           ORDER BY so.observed_at_utc""",
        (run_id,),
    ).fetchall()
    if not rows:
        return None
    snapshot_id = _new_id("topo")
    conn.execute(
        """INSERT INTO topology_snapshots
           (snapshot_id, run_id, source, created_at_utc, confidence)
           VALUES (?, ?, 'cdp', ?, 'medium')""",
        (snapshot_id, run_id, now_iso()),
    )
    edge_keys = set()
    for row in rows:
        payload = _json_value(row["payload_json"]) or {}
        remote_name = str(payload.get("remote_device") or "").strip()
        local_if = str(payload.get("local_interface") or "").strip()
        remote_if = str(payload.get("remote_interface") or "").strip()
        if not remote_name:
            continue
        local_device = conn.execute(
            "SELECT * FROM devices WHERE device_name=?", (row["local_device"],)
        ).fetchone()
        if not local_device:
            continue
        remote_device = _resolve_device_conn(conn, remote_name)
        local_node = _node_conn(
            conn, snapshot_id, local_device["device_name"],
            local_device["device_name"], local_device["device_id"],
        )
        if remote_device:
            remote_node = _node_conn(
                conn, snapshot_id, remote_device["device_name"],
                remote_device["device_name"], remote_device["device_id"],
            )
        else:
            remote_node = _node_conn(
                conn, snapshot_id, f"unmanaged:{remote_name}",
                remote_name, None,
            )
        a_if = _ensure_interface_conn(conn, local_device["device_id"], local_if)
        b_if = (
            _ensure_interface_conn(conn, remote_device["device_id"], remote_if)
            if remote_device and remote_if
            else None
        )
        key = tuple(sorted([local_node, remote_node])) + (a_if, b_if)
        if key in edge_keys or local_node == remote_node:
            continue
        edge_keys.add(key)
        conn.execute(
            """INSERT OR IGNORE INTO topology_edges
               (edge_id, snapshot_id, a_node_id, b_node_id, a_interface_id,
                b_interface_id, link_type, status, source_observation_id, confidence)
               VALUES (?, ?, ?, ?, ?, ?, 'cdp', 'up', ?, 'medium')""",
            (
                _new_id("edge"),
                snapshot_id,
                local_node,
                remote_node,
                a_if,
                b_if,
                row["observation_id"],
            ),
        )
    return snapshot_id


def build_topology_snapshot(run_id: str) -> str | None:
    with get_conn() as conn:
        return _build_topology_conn(conn, run_id)


def get_topology_snapshot(run_id: str | None = None) -> dict | None:
    with get_conn() as conn:
        if run_id:
            row = conn.execute(
                "SELECT * FROM topology_snapshots WHERE run_id=? ORDER BY created_at_utc DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM topology_snapshots ORDER BY created_at_utc DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        nodes = conn.execute(
            "SELECT * FROM topology_nodes WHERE snapshot_id=? ORDER BY node_key",
            (row["snapshot_id"],),
        ).fetchall()
        edges = conn.execute(
            "SELECT * FROM topology_edges WHERE snapshot_id=? ORDER BY edge_id",
            (row["snapshot_id"],),
        ).fetchall()
    return {
        "snapshot": dict(row),
        "nodes": [dict(node) for node in nodes],
        "edges": [dict(edge) for edge in edges],
    }


def _migrate_legacy_devices(conn: sqlite3.Connection) -> None:
    for table in _legacy_tables(conn, "devices_legacy"):
        columns = _columns(conn, table)
        if "device_name" not in columns:
            continue
        for row in conn.execute(f'SELECT * FROM "{table}"').fetchall():
            item = dict(row)
            if "device_id" in columns and item.get("device_id"):
                continue
            item.setdefault("device_key", item.get("device_name"))
            raw_json = item.get("raw_json")
            if raw_json:
                try:
                    item.update(json.loads(raw_json))
                except (TypeError, ValueError):
                    pass
            item["device_name"] = row["device_name"]
            _insert_device_conn(conn, item)


def _migrate_legacy_collection(conn: sqlite3.Connection) -> None:
    legacy_tables = _legacy_tables(conn, "collection_records_legacy")
    if not legacy_tables:
        return
    if conn.execute("SELECT COUNT(*) FROM collection_items").fetchone()[0]:
        return
    records = []
    for table in legacy_tables:
        for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY id').fetchall():
            item = dict(row)
            records.append(
                {
                    "device_name": item.get("device_name"),
                    "status": item.get("status") or "failed",
                    "command": item.get("command"),
                    "raw_output": item.get("raw_output"),
                    "parsed": item.get("parsed_json"),
                    "fail_reason": item.get("fail_reason"),
                    "collected_at": item.get("collected_at"),
                }
            )
    if not records:
        return
    _insert_collection_batch_conn(
        conn,
        records,
        "migration",
        "full",
        "legacy_migration",
        None,
        "batch",
    )


def _seed_command_policies(conn: sqlite3.Connection) -> None:
    from collector.whitelist_commands import READONLY_COMMANDS

    for device_type, commands in READONLY_COMMANDS.items():
        for operation_key, command in commands.items():
            if not command:
                continue
            policy_id = _stable_id("policy", f"{device_type}:{operation_key}:phase1")
            conn.execute(
                """INSERT OR IGNORE INTO command_policies
                   (policy_id, device_type, operation_key, command_template,
                    read_only, timeout_ms, enabled, version)
                   VALUES (?, ?, ?, ?, 1, 15000, 1, 'phase1')""",
                (policy_id, device_type, operation_key, command),
            )
    conn.execute(
        """INSERT OR IGNORE INTO command_policies
           (policy_id, device_type, operation_key, command_template,
            read_only, timeout_ms, enabled, version)
           VALUES (?, 'linux', 'ping_target', 'ping -c 4 -W 2 {target}',
                   1, 15000, 1, 'phase1')""",
        (_stable_id("policy", "linux:ping_target:phase1"),),
    )


def _knowledge_publisher(source: str) -> str:
    lowered = source.lower()
    if "cisco" in lowered and "社区" in source:
        return "Cisco Community"
    if "cisco" in lowered:
        return "Cisco"
    return "通用/社区"


def _knowledge_platform(applicable_device: str | None) -> str:
    value = str(applicable_device or "")
    if "Cisco" in value:
        return "Cisco IOS / IOSvL2"
    if "通用" in value:
        return "通用"
    return value or "未知"


def _sync_knowledge_catalog_conn(
    conn: sqlite3.Connection,
    articles: list[dict],
    *,
    retrieved_at_utc: str | None = None,
) -> dict[str, dict]:
    """同步知识文章、来源和版本，返回 doc_id -> 版本元数据。"""
    result: dict[str, dict] = {}
    timestamp = now_iso()
    retrieved = retrieved_at_utc or timestamp
    for article in articles:
        if not isinstance(article, dict):
            continue
        doc_id = str(article.get("id") or "").strip()
        if not doc_id:
            continue
        title = str(article.get("title") or doc_id)
        category = article.get("category")
        source_title = str(article.get("source") or "未记录来源")
        version_label = str(article.get("version") or "unknown")
        content = str(article.get("content") or "")
        applicable_device = article.get("applicable_device")
        applicable_platform = article.get("applicable_platform") or _knowledge_platform(
            applicable_device
        )
        source_url = article.get("source_url")
        edition = article.get("edition") or version_label
        license_name = article.get("license")
        source_key = _sha256(source_title)[:24]
        source_id = _stable_id("kbsrc", source_key)
        conn.execute(
            """INSERT INTO knowledge_sources
               (source_id, source_key, title, publisher, source_url, edition,
                license, retrieved_at_utc, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(source_key) DO UPDATE SET
                 title=excluded.title,
                 publisher=excluded.publisher,
                 source_url=COALESCE(excluded.source_url, knowledge_sources.source_url),
                 edition=excluded.edition,
                 license=COALESCE(excluded.license, knowledge_sources.license),
                 retrieved_at_utc=excluded.retrieved_at_utc""",
            (
                source_id,
                source_key,
                source_title,
                _knowledge_publisher(source_title),
                source_url,
                edition,
                license_name,
                retrieved,
                timestamp,
            ),
        )

        article_id = _stable_id("kbart", doc_id)
        conn.execute(
            """INSERT INTO knowledge_articles
               (article_id, doc_id, title, category, created_at_utc, updated_at_utc)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(doc_id) DO UPDATE SET
                 title=excluded.title,
                 category=excluded.category,
                 updated_at_utc=excluded.updated_at_utc""",
            (article_id, doc_id, title, category, timestamp, timestamp),
        )

        content_hash = _sha256(
            json.dumps(
                {
                    "title": title,
                    "content": content,
                    "source": source_title,
                    "version": version_label,
                    "applicable_device": applicable_device,
                    "applicable_platform": applicable_platform,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        article_version_id = _stable_id(
            "kbver", f"{doc_id}:{version_label}:{content_hash}"
        )
        conn.execute(
            """INSERT INTO knowledge_article_versions
               (article_version_id, article_id, version_label, source_id,
                applicable_device, applicable_platform, content, content_hash,
                status, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
               ON CONFLICT(article_id, version_label, content_hash) DO UPDATE SET
                 source_id=excluded.source_id,
                 applicable_device=excluded.applicable_device,
                 applicable_platform=excluded.applicable_platform,
                 status='active'""",
            (
                article_version_id,
                article_id,
                version_label,
                source_id,
                applicable_device,
                applicable_platform,
                content,
                content_hash,
                timestamp,
            ),
        )
        conn.execute(
            """UPDATE knowledge_article_versions
               SET status='retired'
               WHERE article_id=? AND article_version_id<>? AND status='active'""",
            (article_id, article_version_id),
        )
        result[doc_id] = {
            "doc_id": doc_id,
            "article_id": article_id,
            "article_version_id": article_version_id,
            "source_id": source_id,
            "source_url": source_url,
            "applicable_platform": applicable_platform,
            "content_hash": content_hash,
            "version_label": version_label,
        }
        conn.execute(
            """UPDATE kb_chunks
               SET article_version_id=?, source_id=?, source_url=?,
                   applicable_platform=?, chunk_no=COALESCE(chunk_no, 1),
                   content_hash=?, is_active=1
               WHERE index_name='ops_knowledge_base' AND doc_id=? AND version=?""",
            (
                article_version_id,
                source_id,
                source_url,
                applicable_platform,
                content_hash,
                doc_id,
                version_label,
            ),
        )
    return result


def _seed_kb_chunks(conn: sqlite3.Connection) -> None:
    """把当前知识库文章固化到关系表，供历史检索结果回看。"""
    articles_path = PROJECT_ROOT / "knowledge_base" / "articles.json"
    if not articles_path.exists():
        return
    try:
        payload = json.loads(articles_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    articles = payload.get("articles", payload) if isinstance(payload, dict) else payload
    if not isinstance(articles, list):
        return
    catalog = _sync_knowledge_catalog_conn(conn, articles)
    active_index = conn.execute(
        """SELECT index_version_id FROM knowledge_index_versions
           WHERE index_name='ops_knowledge_base' AND is_active=1
           ORDER BY created_at_utc DESC LIMIT 1"""
    ).fetchone()
    index_version_id = active_index[0] if active_index else None
    for article in articles:
        doc_id = str(article.get("id") or "").strip()
        if not doc_id:
            continue
        version = str(article.get("version") or "unknown")
        content = str(article.get("content") or "")
        digest = _sha256(
            json.dumps(
                {
                    "title": article.get("title"),
                    "content": content,
                    "source": article.get("source"),
                    "version": version,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        chunk_id = _stable_id("kbchunk", f"ops_knowledge_base:{doc_id}:{version}")
        meta = catalog.get(doc_id, {})
        digest = meta.get("content_hash") or digest
        conn.execute(
            """INSERT INTO kb_chunks
               (chunk_id, index_name, doc_id, title, category, applicable_device,
                version, source, content, content_hash, created_at_utc,
                article_version_id, source_id, index_version_id,
                applicable_platform, source_url, chunk_no, is_active)
               VALUES (?, 'ops_knowledge_base', ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?, 1, 1)
               ON CONFLICT(index_name, doc_id, version) DO UPDATE SET
                 title=excluded.title,
                 category=excluded.category,
                 applicable_device=excluded.applicable_device,
                 source=excluded.source,
                 content=excluded.content,
                 content_hash=excluded.content_hash,
                 article_version_id=excluded.article_version_id,
                 source_id=excluded.source_id,
                 index_version_id=excluded.index_version_id,
                 applicable_platform=excluded.applicable_platform,
                 source_url=excluded.source_url,
                 is_active=1""",
            (
                chunk_id,
                doc_id,
                article.get("title"),
                article.get("category"),
                article.get("applicable_device"),
                version,
                article.get("source"),
                content,
                digest,
                now_iso(),
                meta.get("article_version_id"),
                meta.get("source_id"),
                index_version_id,
                meta.get("applicable_platform"),
                meta.get("source_url"),
            ),
        )


def _seed_tool_registry(conn: sqlite3.Connection) -> None:
    registry_path = PROJECT_ROOT / "network" / "tool_registry.json"
    if not registry_path.exists():
        return
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    tools = payload.get("tools", []) if isinstance(payload, dict) else payload
    if not isinstance(tools, list):
        return
    for tool in tools:
        name = str(tool.get("tool_name") or "").strip()
        if not name:
            continue
        tool_id = _stable_id("tool", name)
        timestamp = now_iso()
        conn.execute(
            """INSERT INTO tool_registry
               (tool_id, tool_name, transport, server_name, description,
                input_schema_json, read_only, requires_approval, enabled,
                version, created_at_utc, updated_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(tool_name) DO UPDATE SET
                 transport=excluded.transport,
                 server_name=excluded.server_name,
                 description=excluded.description,
                 input_schema_json=excluded.input_schema_json,
                 read_only=excluded.read_only,
                 requires_approval=excluded.requires_approval,
                 enabled=excluded.enabled,
                 version=excluded.version,
                 updated_at_utc=excluded.updated_at_utc""",
            (
                tool_id,
                name,
                tool.get("transport") or "local",
                tool.get("server_name"),
                tool.get("description"),
                json.dumps(tool.get("input_schema") or {}, ensure_ascii=False),
                1 if tool.get("read_only", True) else 0,
                1 if tool.get("requires_approval", False) else 0,
                1 if tool.get("enabled", True) else 0,
                str(tool.get("version") or "phase2"),
                timestamp,
                timestamp,
            ),
        )


def _ensure_phase2_columns(conn: sqlite3.Connection) -> None:
    _ensure_column(
        conn,
        "diagnosis_records",
        "session_id",
        "TEXT REFERENCES agent_sessions(session_id)",
    )
    _ensure_column(
        conn,
        "diagnosis_records",
        "conclusion_id",
        "TEXT REFERENCES diagnosis_conclusions(conclusion_id)",
    )
    _ensure_column(
        conn,
        "diagnosis_records",
        "retrieval_query_id",
        "TEXT REFERENCES retrieval_queries(query_id)",
    )
    _ensure_column(
        conn,
        "diagnosis_records",
        "context_snapshot_id",
        "TEXT REFERENCES context_snapshots(snapshot_id)",
    )
    _ensure_column(
        conn,
        "diagnosis_records",
        "model_call_id",
        "TEXT REFERENCES model_calls(call_id)",
    )
    _ensure_column(conn, "diagnosis_records", "kb_status", "TEXT")
    _ensure_column(conn, "diagnosis_records", "total_response_time_ms", "INTEGER")


def _ensure_phase3_columns(conn: sqlite3.Connection) -> None:
    """为二期表补充三期需要保留的知识版本和索引来源字段。"""
    _ensure_column(
        conn, "kb_chunks", "article_version_id",
        "TEXT REFERENCES knowledge_article_versions(article_version_id)",
    )
    _ensure_column(
        conn, "kb_chunks", "source_id",
        "TEXT REFERENCES knowledge_sources(source_id)",
    )
    _ensure_column(
        conn, "kb_chunks", "index_version_id",
        "TEXT REFERENCES knowledge_index_versions(index_version_id)",
    )
    _ensure_column(conn, "kb_chunks", "applicable_platform", "TEXT")
    _ensure_column(conn, "kb_chunks", "source_url", "TEXT")
    _ensure_column(conn, "kb_chunks", "chunk_no", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(
        conn, "kb_chunks", "is_active", "INTEGER NOT NULL DEFAULT 1"
    )
    _ensure_column(
        conn, "retrieval_hits", "article_version_id",
        "TEXT REFERENCES knowledge_article_versions(article_version_id)",
    )
    _ensure_column(
        conn, "retrieval_hits", "index_version_id",
        "TEXT REFERENCES knowledge_index_versions(index_version_id)",
    )
    _ensure_column(conn, "retrieval_hits", "source_url", "TEXT")
    _ensure_column(
        conn, "retrieval_queries", "index_version_id",
        "TEXT REFERENCES knowledge_index_versions(index_version_id)",
    )
    _ensure_column(
        conn, "diagnosis_records", "index_version_id",
        "TEXT REFERENCES knowledge_index_versions(index_version_id)",
    )
    _ensure_column(conn, "knowledge_index_versions", "error_message", "TEXT")


def _seed_phase3_defaults(conn: sqlite3.Connection) -> None:
    """初始化最小角色、权限、用户和保留策略。"""
    timestamp = now_iso()
    roles = {
        "admin": "系统管理员",
        "operator": "运维操作员",
        "approver": "变更审批人",
        "viewer": "只读查看人",
    }
    permissions = {
        "change.create": "创建人工变更",
        "change.approve": "审批人工变更",
        "change.execute": "执行和记录人工操作",
        "knowledge.manage": "管理知识库与索引",
        "evaluation.run": "运行评测实验",
        "audit.read": "查看审计记录",
    }
    role_permissions = {
        "admin": set(permissions),
        "operator": {"change.create", "change.execute", "audit.read"},
        "approver": {"change.approve", "audit.read"},
        "viewer": {"audit.read"},
    }
    for role_name, description in roles.items():
        conn.execute(
            """INSERT OR IGNORE INTO roles
               (role_id, role_name, description, created_at_utc)
               VALUES (?, ?, ?, ?)""",
            (_stable_id("role", role_name), role_name, description, timestamp),
        )
    for code, description in permissions.items():
        conn.execute(
            """INSERT OR IGNORE INTO permissions
               (permission_id, permission_code, description, created_at_utc)
               VALUES (?, ?, ?, ?)""",
            (_stable_id("permission", code), code, description, timestamp),
        )
    for role_name, codes in role_permissions.items():
        role_id = _stable_id("role", role_name)
        for code in codes:
            conn.execute(
                """INSERT OR IGNORE INTO role_permissions (role_id, permission_id)
                   VALUES (?, ?)""",
                (role_id, _stable_id("permission", code)),
            )
    users = {
        "admin": ("系统管理员", "admin"),
        "system": ("系统服务", "operator"),
    }
    for username, (display_name, role_name) in users.items():
        user_id = _stable_id("user", username)
        conn.execute(
            """INSERT OR IGNORE INTO users
               (user_id, username, display_name, status, created_at_utc)
               VALUES (?, ?, ?, 'active', ?)""",
            (user_id, username, display_name, timestamp),
        )
        conn.execute(
            """INSERT OR IGNORE INTO user_roles (user_id, role_id, granted_at_utc)
               VALUES (?, ?, ?)""",
            (user_id, _stable_id("role", role_name), timestamp),
        )
    policies = [
        (
            "health_checks_90d",
            "健康检查保留90天",
            "health_checks",
            90,
            "delete",
            1,
        ),
        (
            "orphan_blobs_180d",
            "孤立原始内容保留180天",
            "raw_blobs",
            180,
            "delete_orphan",
            1,
        ),
        (
            "audit_log_365d_dry_run",
            "审计日志保留365天（仅检查）",
            "audit_log",
            365,
            "dry_run",
            1,
        ),
    ]
    for name, label, target, age_days, action, enabled in policies:
        conn.execute(
            """INSERT OR IGNORE INTO retention_policies
               (policy_id, name, target_table, age_days, action, enabled,
                config_json, created_at_utc, updated_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)""",
            (
                _stable_id("retention", name),
                label,
                target,
                age_days,
                action,
                enabled,
                timestamp,
                timestamp,
            ),
        )


def _backfill_legacy_diagnoses(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """SELECT * FROM diagnosis_records
           WHERE session_id IS NULL OR conclusion_id IS NULL
           ORDER BY id"""
    ).fetchall()
    for row in rows:
        diagnosis_id = row["id"]
        session_id = row["session_id"] or _stable_id("session", f"legacy-diagnosis:{diagnosis_id}")
        timestamp = _to_utc_iso(row["created_at"]) or now_iso()
        question = row["question"] or ""
        answer = _json_value(row["answer_json"]) or {}
        evidence = _json_value(row["evidence_json"]) or []
        retrieved_ids = _json_value(row["retrieved_doc_ids_json"]) or []
        conn.execute(
            """INSERT OR IGNORE INTO agent_sessions
               (session_id, title, created_by, model_id, prompt_version_id, status,
                started_at_utc, updated_at_utc, ended_at_utc)
               VALUES (?, ?, 'legacy_import', ?, NULL, 'completed', ?, ?, ?)""",
            (
                session_id,
                (question[:80] or f"历史诊断 #{diagnosis_id}"),
                row["model_id"],
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        user_message_id = _stable_id("msg", f"{session_id}:1")
        assistant_message_id = _stable_id("msg", f"{session_id}:2")
        conn.execute(
            """INSERT OR IGNORE INTO agent_messages
               (message_id, session_id, seq_no, role, content_text, content_json, created_at_utc)
               VALUES (?, ?, 1, 'user', ?, ?, ?)""",
            (
                user_message_id,
                session_id,
                question,
                json.dumps({"question": question}, ensure_ascii=False),
                timestamp,
            ),
        )
        conn.execute(
            """INSERT OR IGNORE INTO agent_messages
               (message_id, session_id, seq_no, role, content_text, content_json, created_at_utc)
               VALUES (?, ?, 2, 'assistant', ?, ?, ?)""",
            (
                assistant_message_id,
                session_id,
                json.dumps(answer, ensure_ascii=False),
                json.dumps(answer, ensure_ascii=False),
                timestamp,
            ),
        )
        snapshot_id = row["context_snapshot_id"] or _stable_id(
            "ctx", f"legacy-diagnosis:{diagnosis_id}"
        )
        snapshot_text = json.dumps(
            {
                "question": question,
                "evidence": evidence,
                "retrieved_doc_ids": retrieved_ids,
            },
            ensure_ascii=False,
        )
        conn.execute(
            """INSERT OR IGNORE INTO context_snapshots
               (snapshot_id, session_id, message_id, prompt_version_id,
                rendered_messages_json, rendered_prompt_redacted, prompt_hash,
                token_count, created_at_utc)
               VALUES (?, ?, ?, NULL, ?, NULL, ?, ?, ?)""",
            (
                snapshot_id,
                session_id,
                user_message_id,
                snapshot_text,
                _sha256(snapshot_text),
                max(1, len(snapshot_text) // 4),
                timestamp,
            ),
        )
        query_id = row["retrieval_query_id"] or _stable_id(
            "retrieval", f"legacy-diagnosis:{diagnosis_id}"
        )
        conn.execute(
            """INSERT OR IGNORE INTO retrieval_queries
               (query_id, session_id, message_id, query_text, filters_json,
                top_k, status, error_message, created_at_utc)
               VALUES (?, ?, ?, ?, NULL, ?, ?, NULL, ?)""",
            (
                query_id,
                session_id,
                user_message_id,
                question,
                len(retrieved_ids) if retrieved_ids else 4,
                "success" if retrieved_ids else "empty",
                timestamp,
            ),
        )
        for rank, doc_id in enumerate(retrieved_ids, 1):
            chunk = conn.execute(
                """SELECT * FROM kb_chunks
                   WHERE index_name='ops_knowledge_base' AND doc_id=?
                   ORDER BY created_at_utc DESC LIMIT 1""",
                (doc_id,),
            ).fetchone()
            hit_id = _stable_id("hit", f"{query_id}:{rank}:{doc_id}")
            conn.execute(
                """INSERT OR IGNORE INTO retrieval_hits
                   (hit_id, query_id, chunk_id, rank_no, distance, doc_id, title,
                    category, applicable_device, version, source, content,
                    content_hash, created_at_utc)
                   VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    hit_id,
                    query_id,
                    chunk["chunk_id"] if chunk else None,
                    rank,
                    str(doc_id),
                    chunk["title"] if chunk else None,
                    chunk["category"] if chunk else None,
                    chunk["applicable_device"] if chunk else None,
                    chunk["version"] if chunk else None,
                    chunk["source"] if chunk else None,
                    chunk["content"] if chunk else None,
                    chunk["content_hash"] if chunk else None,
                    timestamp,
                ),
            )
        model_call_id = row["model_call_id"] or _stable_id(
            "modelcall", f"legacy-diagnosis:{diagnosis_id}"
        )
        conn.execute(
            """INSERT OR IGNORE INTO model_calls
               (call_id, session_id, message_id, context_snapshot_id, provider,
                model, request_id, params_json, input_tokens, output_tokens,
                latency_ms, status, error_code, error_message, response_blob_id,
                created_at_utc)
               VALUES (?, ?, ?, ?, 'legacy_unknown', ?, NULL, ?, NULL, NULL,
                       ?, 'success', NULL, NULL, NULL, ?)""",
            (
                model_call_id,
                session_id,
                assistant_message_id,
                snapshot_id,
                row["model_id"],
                row["llm_params_json"],
                row["response_time_ms"],
                timestamp,
            ),
        )
        conclusion_id = row["conclusion_id"] or _stable_id(
            "conclusion", f"legacy-diagnosis:{diagnosis_id}"
        )
        causes = answer.get("possible_causes") or []
        root_cause = ""
        if causes and isinstance(causes[0], dict):
            root_cause = str(causes[0].get("cause") or "")
        conn.execute(
            """INSERT OR IGNORE INTO diagnosis_conclusions
               (conclusion_id, diagnosis_id, session_id, final_message_id,
                fault_phenomenon, root_cause, missing_information, confidence,
                created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                conclusion_id,
                diagnosis_id,
                session_id,
                assistant_message_id,
                answer.get("fault_phenomenon"),
                root_cause or None,
                answer.get("missing_information"),
                None,
                timestamp,
            ),
        )
        for evidence_item in evidence:
            if not isinstance(evidence_item, dict):
                continue
            observation_id = evidence_item.get("observation_id")
            item_id = evidence_item.get("item_id")
            if observation_id and not conn.execute(
                "SELECT 1 FROM state_observations WHERE observation_id=?",
                (observation_id,),
            ).fetchone():
                observation_id = None
            if item_id and not conn.execute(
                "SELECT 1 FROM collection_items WHERE item_id=?",
                (item_id,),
            ).fetchone():
                item_id = None
            if not observation_id and not item_id:
                continue
            conn.execute(
                """INSERT OR IGNORE INTO evidence_links
                   (link_id, conclusion_id, link_type, observation_id, item_id,
                    retrieval_hit_id, tool_call_result_id, claim, role, created_at_utc)
                   VALUES (?, ?, 'device_evidence', ?, ?, NULL, NULL, ?, 'context', ?)""",
                (
                    _stable_id(
                        "evlink",
                        f"{conclusion_id}:{observation_id or item_id}",
                    ),
                    conclusion_id,
                    observation_id,
                    item_id,
                    evidence_item.get("summary"),
                    timestamp,
                ),
            )
        conn.execute(
            """UPDATE diagnosis_records
               SET session_id=?, conclusion_id=?, retrieval_query_id=?,
                   context_snapshot_id=?, model_call_id=?, kb_status=?,
                   total_response_time_ms=?
               WHERE id=?""",
            (
                session_id,
                conclusion_id,
                query_id,
                snapshot_id,
                model_call_id,
                "success" if retrieved_ids else "empty",
                row["response_time_ms"],
                diagnosis_id,
            ),
        )


def _legacy_change_status(value: str | None) -> str:
    mapping = {
        "confirmed": "approved",
        "completed": "completed",
        "rejected": "rejected",
        "backup_failed": "failed",
        "recheck_failed": "failed",
    }
    return mapping.get(str(value or ""), "draft")


def _backfill_legacy_manual_actions(conn: sqlite3.Connection) -> None:
    """把旧 manual_actions 转成可查看的历史变更，保留原 ID 和文本。"""
    if not _table_exists(conn, "manual_actions"):
        return
    rows = conn.execute("SELECT * FROM manual_actions ORDER BY id").fetchall()
    for row in rows:
        legacy_id = int(row["id"])
        if conn.execute(
            "SELECT 1 FROM change_requests WHERE legacy_action_id=?",
            (legacy_id,),
        ).fetchone():
            continue
        timestamp = _to_utc_iso(row["created_at"]) or now_iso()
        operator = row["operator"] or "legacy"
        description = row["action_desc"] or "历史人工操作"
        status = _legacy_change_status(row["status"])
        diagnosis_id = row["diagnosis_id"]
        diagnosis_row = None
        conclusion_id = None
        session_id = None
        if diagnosis_id:
            diagnosis_row = conn.execute(
                "SELECT * FROM diagnosis_records WHERE id=?",
                (diagnosis_id,),
            ).fetchone()
            if diagnosis_row:
                conclusion_id = diagnosis_row["conclusion_id"]
                session_id = diagnosis_row["session_id"]
        change_id = _stable_id("change", f"legacy-action:{legacy_id}")
        conn.execute(
            """INSERT INTO change_requests
               (change_id, conclusion_id, diagnosis_id, session_id, device_id,
                device_name, change_type, title, description, risk_level, status,
                requested_by, approved_by, planned_at_utc, started_at_utc,
                finished_at_utc, rollback_plan, legacy_action_id,
                created_at_utc, updated_at_utc)
               VALUES (?, ?, ?, ?, NULL, '未指定设备', 'software', ?, ?, 'medium',
                       ?, ?, NULL, NULL, ?, ?, NULL, ?, ?, ?)""",
            (
                change_id,
                conclusion_id,
                diagnosis_id if diagnosis_row else None,
                session_id,
                (description[:100] or "历史人工操作"),
                description,
                status,
                operator,
                operator if status in ("approved", "completed") else None,
                timestamp if status in ("executing", "completed", "failed") else None,
                timestamp if status in ("completed", "failed") else None,
                legacy_id,
                timestamp,
                timestamp,
            ),
        )
        conn.execute(
            """INSERT INTO change_events
               (event_id, change_id, event_type, actor, from_status, to_status,
                payload_json, payload_hash, created_at_utc)
               VALUES (?, ?, 'legacy_imported', ?, NULL, ?, ?, ?, ?)""",
            (
                _new_id("changeevent"),
                change_id,
                operator,
                status,
                json.dumps(
                    {"legacy_action_id": legacy_id, "raw_status": row["status"]},
                    ensure_ascii=False,
                ),
                _sha256(f"legacy-action:{legacy_id}:{row['status']}"),
                timestamp,
            ),
        )
        step_status = "success" if status == "completed" else "pending"
        conn.execute(
            """INSERT INTO change_steps
               (step_id, change_id, seq_no, step_type, source_type, title,
                command_text, expected_result, requires_approval, status,
                executed_by, started_at_utc, finished_at_utc, result_summary,
                created_at_utc, updated_at_utc)
               VALUES (?, ?, 1, 'software', 'human', ?, ?, NULL, 0, ?, ?, ?, ?, ?, ?, ?)""",
            (
                _stable_id("step", f"legacy-action:{legacy_id}:1"),
                change_id,
                (description[:100] or "历史人工操作"),
                description,
                step_status,
                operator,
                timestamp,
                timestamp if step_status == "success" else None,
                row["recheck_result"],
                timestamp,
                timestamp,
            ),
        )
        if row["backup_ref"]:
            conn.execute(
                """INSERT INTO config_backups
                   (backup_id, change_id, device_id, config_snapshot_id, blob_id,
                    sha256, captured_at_utc, backup_type, verified, storage_ref,
                    restore_ref, created_at_utc)
                   VALUES (?, ?, NULL, NULL, NULL, NULL, ?, 'before', 0, ?, NULL, ?)""",
                (
                    _stable_id("backup", f"legacy-action:{legacy_id}"),
                    change_id,
                    timestamp,
                    row["backup_ref"],
                    timestamp,
                ),
            )
        if row["recheck_result"]:
            conn.execute(
                """INSERT INTO rechecks
                   (recheck_id, change_id, collection_run_id, source_device_id,
                    source_device_name, target, check_type, expected_json,
                    actual_json, pass_fail, evidence_observation_id,
                    executed_at_utc, created_at_utc)
                   VALUES (?, ?, NULL, NULL, NULL, 'legacy', 'ping', NULL, ?,
                           ?, NULL, ?, ?)""",
                (
                    _stable_id("recheck", f"legacy-action:{legacy_id}"),
                    change_id,
                    json.dumps({"recheck_result": row["recheck_result"]}, ensure_ascii=False),
                    "pass" if status == "completed" else "fail",
                    timestamp,
                    timestamp,
                ),
            )


def _normalize_existing_timestamps(conn: sqlite3.Connection) -> None:
    """把一期旧数据中的本地时区时间统一转换为 UTC。"""
    table_columns = {
        "sites": ("created_at_utc",),
        "devices": ("created_at_utc", "updated_at_utc"),
        "device_aliases": ("created_at_utc",),
        "device_endpoints": ("last_verified_at_utc",),
        "interfaces": ("first_seen_at_utc", "last_seen_at_utc"),
        "inventory_versions": ("captured_at_utc",),
        "raw_blobs": ("created_at_utc",),
        "collection_runs": (
            "started_at_utc",
            "finished_at_utc",
            "published_at_utc",
            "created_at_utc",
        ),
        "device_capture_results": (
            "started_at_utc",
            "finished_at_utc",
            "published_at_utc",
        ),
        "collection_items": (
            "started_at_utc",
            "finished_at_utc",
            "created_at_utc",
        ),
        "state_observations": ("observed_at_utc", "created_at_utc"),
        "config_snapshots": ("captured_at_utc",),
        "topology_snapshots": ("created_at_utc",),
        "prompt_versions": ("created_at_utc",),
        "agent_sessions": ("started_at_utc", "updated_at_utc", "ended_at_utc"),
        "agent_messages": ("created_at_utc",),
        "context_snapshots": ("created_at_utc",),
        "tool_registry": ("created_at_utc", "updated_at_utc"),
        "tool_calls": ("started_at_utc", "finished_at_utc", "created_at_utc"),
        "tool_call_results": ("created_at_utc",),
        "model_calls": ("created_at_utc",),
        "kb_chunks": ("created_at_utc",),
        "retrieval_queries": ("created_at_utc",),
        "retrieval_hits": ("created_at_utc",),
        "diagnosis_conclusions": ("created_at_utc",),
        "evidence_links": ("created_at_utc",),
        "change_requests": (
            "planned_at_utc",
            "started_at_utc",
            "finished_at_utc",
            "created_at_utc",
            "updated_at_utc",
        ),
        "change_steps": (
            "started_at_utc",
            "finished_at_utc",
            "created_at_utc",
            "updated_at_utc",
        ),
        "change_approvals": ("created_at_utc",),
        "change_events": ("created_at_utc",),
        "command_executions": (
            "started_at_utc",
            "finished_at_utc",
            "created_at_utc",
        ),
        "physical_actions": ("performed_at_utc", "created_at_utc"),
        "config_backups": ("captured_at_utc", "created_at_utc"),
        "rechecks": ("executed_at_utc", "created_at_utc"),
        "attachments": ("created_at_utc",),
        "health_checks": ("checked_at_utc",),
        "audit_log": ("created_at_utc",),
        "reports": ("generated_at_utc",),
        "diagnosis_records": ("created_at",),
        "manual_actions": ("created_at",),
        "system_status": ("updated_at",),
    }
    for table, columns in table_columns.items():
        if not _table_exists(conn, table):
            continue
        column_sql = ", ".join(columns)
        rows = conn.execute(
            f'SELECT rowid, {column_sql} FROM "{table}"'
        ).fetchall()
        for row in rows:
            updates = {}
            for column in columns:
                normalized = _to_utc_iso(row[column])
                if normalized != row[column]:
                    updates[column] = normalized
            if not updates:
                continue
            assignments = ", ".join(f"{column}=?" for column in updates)
            conn.execute(
                f'UPDATE "{table}" SET {assignments} WHERE rowid=?',
                (*updates.values(), row["rowid"]),
            )


def _ensure_compatibility_view(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "collection_records"):
        return
    if _view_exists(conn, "collection_records"):
        conn.execute("DROP VIEW collection_records")
    conn.execute(
        """CREATE VIEW collection_records AS
           SELECT ci.item_id AS id,
                  d.device_name AS device_name,
                  COALESCE(ci.finished_at_utc, ci.started_at_utc) AS collected_at,
                  CASE WHEN ci.collection_status='success' THEN 'success' ELSE 'failed' END AS status,
                  ci.error_message AS fail_reason,
                  ci.command_or_tool AS command,
                  rb.content_text AS raw_output,
                  ci.parsed_json AS parsed_json
           FROM collection_items ci
           JOIN devices d ON d.device_id=ci.device_id
           LEFT JOIN raw_blobs rb ON rb.blob_id=ci.raw_blob_id"""
    )


def _prepare_schema(conn: sqlite3.Connection) -> None:
    if _needs_legacy_migration(conn):
        _rename_legacy_tables(conn)
    conn.executescript(BASE_SCHEMA)
    needs_time_normalization = not conn.execute(
        "SELECT 1 FROM schema_migrations WHERE version=2"
    ).fetchone()
    _migrate_legacy_devices(conn)
    _migrate_legacy_collection(conn)
    _seed_command_policies(conn)
    if needs_time_normalization:
        _normalize_existing_timestamps(conn)
    _ensure_compatibility_view(conn)
    conn.execute(
        """INSERT OR IGNORE INTO schema_migrations (version, name, applied_at_utc)
           VALUES (?, 'phase1_asset_collection_state', ?)""",
        (1, now_iso()),
    )
    conn.execute(
        """INSERT OR IGNORE INTO schema_migrations (version, name, applied_at_utc)
           VALUES (?, 'normalize_utc_timestamps', ?)""",
        (2, now_iso()),
    )
    if _schema_version(conn) < 3:
        conn.executescript(PHASE2_SCHEMA)
        _ensure_phase2_columns(conn)
        _backfill_legacy_diagnoses(conn)
        conn.execute(
            """INSERT OR IGNORE INTO schema_migrations (version, name, applied_at_utc)
               VALUES (3, 'phase2_agent_sessions_tools_and_citations', ?)""",
            (now_iso(),),
        )
    if _schema_version(conn) < 4:
        conn.executescript(PHASE2B_SCHEMA)
        _backfill_legacy_manual_actions(conn)
        conn.execute(
            """INSERT OR IGNORE INTO schema_migrations (version, name, applied_at_utc)
               VALUES (4, 'phase2b_human_change_and_operations', ?)""",
            (now_iso(),),
        )
    if _schema_version(conn) < 5:
        conn.executescript(PHASE3_SCHEMA)
        _ensure_phase3_columns(conn)
        _seed_phase3_defaults(conn)
        conn.execute(
            """INSERT OR IGNORE INTO schema_migrations (version, name, applied_at_utc)
               VALUES (5, 'phase3_knowledge_evaluation_and_governance', ?)""",
            (now_iso(),),
        )
    _seed_kb_chunks(conn)
    _seed_tool_registry(conn)


def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _backup_before_upgrade(conn)
        conn.execute("BEGIN")
        _prepare_schema(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def insert_diagnosis(question, model_id, prompt_template_id, llm_params,
                      retrieved_doc_ids, evidence, answer, use_kb=1, response_time_ms=None):
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO diagnosis_records
               (created_at, question, model_id, prompt_template_id, llm_params_json,
                retrieved_doc_ids_json, evidence_json, answer_json, use_kb, response_time_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (now_iso(), question, model_id, prompt_template_id,
             json.dumps(llm_params, ensure_ascii=False),
             json.dumps(retrieved_doc_ids, ensure_ascii=False),
             json.dumps(evidence, ensure_ascii=False),
             json.dumps(answer, ensure_ascii=False),
             use_kb, response_time_ms),
        )
        return cur.lastrowid


def list_diagnoses(limit=50):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM diagnosis_records ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_diagnosis(diagnosis_id: int):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM diagnosis_records WHERE id=?", (diagnosis_id,)
        ).fetchone()
    return dict(row) if row else None


def _next_message_seq_conn(conn: sqlite3.Connection, session_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(seq_no), 0) FROM agent_messages WHERE session_id=?",
        (session_id,),
    ).fetchone()
    return int(row[0] or 0) + 1


def _kb_chunk_for_hit_conn(conn: sqlite3.Connection, hit: dict) -> sqlite3.Row | None:
    doc_id = str(hit.get("id") or hit.get("doc_id") or "")
    if not doc_id:
        return None
    if hit.get("version"):
        row = conn.execute(
            """SELECT * FROM kb_chunks
               WHERE index_name='ops_knowledge_base' AND doc_id=? AND version=?
               LIMIT 1""",
            (doc_id, hit.get("version")),
        ).fetchone()
        if row:
            return row
    return conn.execute(
        """SELECT * FROM kb_chunks
           WHERE index_name='ops_knowledge_base' AND doc_id=?
           ORDER BY created_at_utc DESC LIMIT 1""",
        (doc_id,),
    ).fetchone()


def _row_value(row: sqlite3.Row | None, key: str, default=None):
    if row is None or key not in row.keys():
        return default
    return row[key]


def _existing_id_conn(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    value: str | None,
) -> str | None:
    if not value:
        return None
    row = conn.execute(
        f'SELECT 1 FROM "{table}" WHERE "{column}"=? LIMIT 1',
        (value,),
    ).fetchone()
    return value if row else None


def save_diagnosis_run(
    *,
    question: str,
    model_id: str,
    prompt_template_id: str,
    prompt_version: dict | None,
    llm_params: dict | None,
    answer: dict,
    evidence: list,
    retrieved_docs: list,
    use_kb: bool,
    kb_status: str,
    kb_error: str | None,
    response_time_ms: int | None,
    total_response_time_ms: int | None,
    context_messages: list,
    requested_by: str = "web",
    session_id: str | None = None,
    model_status: str = "success",
    model_error_code: str | None = None,
    model_error_message: str | None = None,
    retrieval_filters: dict | None = None,
    top_k: int = 4,
) -> dict:
    """原子写入一次诊断的会话、上下文、检索、模型调用和结论引用。"""
    init_db()
    timestamp = now_iso()
    prompt_version_id = None
    params = dict(llm_params or {})
    answer_text = json.dumps(answer, ensure_ascii=False, sort_keys=True)

    with get_conn() as conn:
        if prompt_version:
            system_prompt = str(prompt_version.get("system_prompt") or "")
            user_template = str(prompt_version.get("user_template") or "")
            prompt_hash = _sha256(system_prompt + "\n" + user_template)
            prompt_version_id = _stable_id(
                "promptver", f"{prompt_template_id}:{prompt_hash}"
            )
            conn.execute(
                """INSERT OR IGNORE INTO prompt_versions
                   (prompt_version_id, template_id, system_prompt, user_template,
                    content_hash, is_active, created_at_utc)
                   VALUES (?, ?, ?, ?, ?, 1, ?)""",
                (
                    prompt_version_id,
                    prompt_template_id,
                    system_prompt,
                    user_template,
                    prompt_hash,
                    timestamp,
                ),
            )

        if session_id:
            session = conn.execute(
                "SELECT * FROM agent_sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
            if not session:
                raise ValueError(f"诊断会话不存在: {session_id}")
        else:
            session_id = _new_id("session")
            session = None

        session_status = "completed" if model_status == "success" else "failed"
        if session:
            conn.execute(
                """UPDATE agent_sessions
                   SET model_id=?, prompt_version_id=COALESCE(?, prompt_version_id),
                       status=?, updated_at_utc=?, ended_at_utc=?
                   WHERE session_id=?""",
                (
                    model_id,
                    prompt_version_id,
                    session_status,
                    timestamp,
                    timestamp,
                    session_id,
                ),
            )
        else:
            conn.execute(
                """INSERT INTO agent_sessions
                   (session_id, title, created_by, model_id, prompt_version_id,
                    status, started_at_utc, updated_at_utc, ended_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    (question[:100] or "未命名诊断会话"),
                    requested_by,
                    model_id,
                    prompt_version_id,
                    session_status,
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )

        seq = _next_message_seq_conn(conn, session_id)
        user_message_id = _new_id("msg")
        conn.execute(
            """INSERT INTO agent_messages
               (message_id, session_id, seq_no, role, content_text, content_json, created_at_utc)
               VALUES (?, ?, ?, 'user', ?, ?, ?)""",
            (
                user_message_id,
                session_id,
                seq,
                question,
                json.dumps({"question": question}, ensure_ascii=False),
                timestamp,
            ),
        )

        rendered_messages_json = json.dumps(context_messages, ensure_ascii=False)
        rendered_prompt_redacted = "\n\n".join(
            str(message.get("content") or "")
            for message in context_messages
            if isinstance(message, dict)
        )
        snapshot_id = _new_id("ctx")
        conn.execute(
            """INSERT INTO context_snapshots
               (snapshot_id, session_id, message_id, prompt_version_id,
                rendered_messages_json, rendered_prompt_redacted, prompt_hash,
                token_count, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot_id,
                session_id,
                user_message_id,
                prompt_version_id,
                rendered_messages_json,
                rendered_prompt_redacted,
                _sha256(rendered_messages_json),
                max(1, len(rendered_prompt_redacted) // 4),
                timestamp,
            ),
        )

        normalized_kb_status = (
            kb_status if kb_status in ("success", "empty", "disabled", "error") else "error"
        )
        kb_index_version_id = next(
            (
                hit.get("index_version_id")
                for hit in (retrieved_docs or [])
                if hit.get("index_version_id")
            ),
            None,
        )
        kb_index_version_id = _existing_id_conn(
            conn,
            "knowledge_index_versions",
            "index_version_id",
            kb_index_version_id,
        )
        query_id = _new_id("retrieval")
        conn.execute(
            """INSERT INTO retrieval_queries
               (query_id, session_id, message_id, query_text, filters_json,
                top_k, index_version_id, status, error_message, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                query_id,
                session_id,
                user_message_id,
                question,
                _json_text(retrieval_filters),
                top_k,
                kb_index_version_id,
                normalized_kb_status,
                kb_error,
                timestamp,
            ),
        )
        hits_by_doc_id: dict[str, list[dict]] = {}
        for rank, hit in enumerate(retrieved_docs or [], 1):
            chunk = _kb_chunk_for_hit_conn(conn, hit)
            doc_id = str(hit.get("id") or hit.get("doc_id") or "")
            if not doc_id:
                continue
            hit_id = _new_id("hit")
            content = hit.get("content") or (chunk["content"] if chunk else None)
            content_hash = (
                chunk["content_hash"] if chunk else _sha256(str(content or ""))
            )
            article_version_id = (
                hit.get("article_version_id")
                or _row_value(chunk, "article_version_id")
            )
            article_version_id = _existing_id_conn(
                conn,
                "knowledge_article_versions",
                "article_version_id",
                article_version_id,
            )
            index_version_id = (
                hit.get("index_version_id")
                or _row_value(chunk, "index_version_id")
                or kb_index_version_id
            )
            index_version_id = _existing_id_conn(
                conn,
                "knowledge_index_versions",
                "index_version_id",
                index_version_id,
            )
            source_url = (
                hit.get("source_url")
                or _row_value(chunk, "source_url")
            )
            conn.execute(
                """INSERT INTO retrieval_hits
                   (hit_id, query_id, chunk_id, rank_no, distance, doc_id, title,
                    category, applicable_device, version, source, content,
                    content_hash, article_version_id, index_version_id,
                    source_url, created_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    hit_id,
                    query_id,
                    chunk["chunk_id"] if chunk else None,
                    rank,
                    hit.get("distance"),
                    doc_id,
                    hit.get("title") or (chunk["title"] if chunk else None),
                    hit.get("category") or (chunk["category"] if chunk else None),
                    hit.get("applicable_device")
                    or (chunk["applicable_device"] if chunk else None),
                    hit.get("version") or (chunk["version"] if chunk else None),
                    hit.get("source") or (chunk["source"] if chunk else None),
                    content,
                    content_hash,
                    article_version_id,
                    index_version_id,
                    source_url,
                    timestamp,
                ),
            )
            hit_record = {
                "hit_id": hit_id,
                "doc_id": doc_id,
                "rank_no": rank,
                "chunk": dict(chunk) if chunk else None,
            }
            hits_by_doc_id.setdefault(doc_id, []).append(hit_record)

        seq = _next_message_seq_conn(conn, session_id)
        assistant_message_id = _new_id("msg")
        conn.execute(
            """INSERT INTO agent_messages
               (message_id, session_id, seq_no, role, content_text, content_json, created_at_utc)
               VALUES (?, ?, ?, 'assistant', ?, ?, ?)""",
            (
                assistant_message_id,
                session_id,
                seq,
                answer_text,
                answer_text,
                timestamp,
            ),
        )

        provider = str(params.get("_provider") or "openai_compatible")
        usage = params.get("_usage") or {}
        public_params = {
            key: value for key, value in params.items() if not key.startswith("_")
        }
        model_call_id = _new_id("modelcall")
        response_blob_id = _store_blob_conn(conn, answer_text, "application/json")
        conn.execute(
            """INSERT INTO model_calls
               (call_id, session_id, message_id, context_snapshot_id, provider,
                model, request_id, params_json, input_tokens, output_tokens,
                latency_ms, status, error_code, error_message, response_blob_id,
                created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                model_call_id,
                session_id,
                assistant_message_id,
                snapshot_id,
                provider,
                model_id,
                params.get("_request_id"),
                json.dumps(public_params, ensure_ascii=False),
                usage.get("prompt_tokens") if isinstance(usage, dict) else None,
                usage.get("completion_tokens") if isinstance(usage, dict) else None,
                response_time_ms,
                model_status if model_status in ("success", "error") else "error",
                model_error_code,
                model_error_message,
                response_blob_id,
                timestamp,
            ),
        )

        retrieved_doc_ids = [
            str(hit.get("id") or hit.get("doc_id"))
            for hit in (retrieved_docs or [])
            if hit.get("id") or hit.get("doc_id")
        ]
        cur = conn.execute(
            """INSERT INTO diagnosis_records
               (created_at, question, model_id, prompt_template_id, llm_params_json,
                retrieved_doc_ids_json, evidence_json, answer_json, use_kb,
                response_time_ms, session_id, retrieval_query_id,
                context_snapshot_id, model_call_id, kb_status,
                total_response_time_ms, index_version_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                timestamp,
                question,
                model_id,
                prompt_template_id,
                json.dumps(public_params, ensure_ascii=False),
                json.dumps(retrieved_doc_ids, ensure_ascii=False),
                json.dumps(evidence, ensure_ascii=False),
                json.dumps(answer, ensure_ascii=False),
                1 if use_kb else 0,
                response_time_ms,
                session_id,
                query_id,
                snapshot_id,
                model_call_id,
                normalized_kb_status,
                total_response_time_ms,
                kb_index_version_id,
            ),
        )
        diagnosis_id = int(cur.lastrowid)

        causes = answer.get("possible_causes") or []
        root_cause = ""
        if causes and isinstance(causes[0], dict):
            root_cause = str(causes[0].get("cause") or "")
        confidence = (
            "high"
            if model_status == "success" and evidence and retrieved_docs
            else "medium" if model_status == "success" and evidence
            else "low"
        )
        conclusion_id = _new_id("conclusion")
        conn.execute(
            """INSERT INTO diagnosis_conclusions
               (conclusion_id, diagnosis_id, session_id, final_message_id,
                fault_phenomenon, root_cause, missing_information, confidence,
                created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                conclusion_id,
                diagnosis_id,
                session_id,
                assistant_message_id,
                answer.get("fault_phenomenon"),
                root_cause or None,
                answer.get("missing_information"),
                confidence,
                timestamp,
            ),
        )
        conn.execute(
            "UPDATE diagnosis_records SET conclusion_id=? WHERE id=?",
            (conclusion_id, diagnosis_id),
        )

        referenced_observation_ids = set()
        referenced_evidence_pairs = set()
        for item in answer.get("evidence_used") or []:
            if not isinstance(item, dict):
                continue
            if item.get("observation_id"):
                referenced_observation_ids.add(item["observation_id"])
            if item.get("device_name") and item.get("collected_at"):
                referenced_evidence_pairs.add(
                    (item["device_name"], item["collected_at"])
                )

        for evidence_item in evidence:
            if not isinstance(evidence_item, dict):
                continue
            observation_id = evidence_item.get("observation_id")
            item_id = evidence_item.get("item_id")
            if not observation_id and not item_id:
                continue
            if observation_id and not conn.execute(
                "SELECT 1 FROM state_observations WHERE observation_id=?",
                (observation_id,),
            ).fetchone():
                observation_id = None
            if item_id and not conn.execute(
                "SELECT 1 FROM collection_items WHERE item_id=?",
                (item_id,),
            ).fetchone():
                item_id = None
            if not observation_id and not item_id:
                continue
            referenced = bool(
                observation_id in referenced_observation_ids
                or (
                    evidence_item.get("device_name"),
                    evidence_item.get("collected_at"),
                )
                in referenced_evidence_pairs
            )
            conn.execute(
                """INSERT INTO evidence_links
                   (link_id, conclusion_id, link_type, observation_id, item_id,
                    retrieval_hit_id, tool_call_result_id, claim, role, created_at_utc)
                   VALUES (?, ?, 'device_evidence', ?, ?, NULL, NULL, ?, ?, ?)""",
                (
                    _new_id("evlink"),
                    conclusion_id,
                    observation_id,
                    item_id,
                    evidence_item.get("summary"),
                    "supporting" if referenced else "context",
                    timestamp,
                ),
            )

        referenced_kb_ids = set()
        for key in ("possible_causes", "fix_suggestions"):
            for item in answer.get(key) or []:
                if isinstance(item, dict):
                    referenced_kb_ids.update(
                        str(ref) for ref in (item.get("kb_refs") or [])
                    )
        for doc_id, hit_records in hits_by_doc_id.items():
            for hit_record in hit_records:
                conn.execute(
                    """INSERT INTO evidence_links
                       (link_id, conclusion_id, link_type, observation_id, item_id,
                        retrieval_hit_id, tool_call_result_id, claim, role, created_at_utc)
                       VALUES (?, ?, 'knowledge', NULL, NULL, ?, NULL, ?, ?, ?)""",
                    (
                        _new_id("evlink"),
                        conclusion_id,
                        hit_record["hit_id"],
                        f"检索知识片段 {doc_id}",
                        "supporting" if doc_id in referenced_kb_ids else "context",
                        timestamp,
                    ),
                )

    return {
        "diagnosis_id": diagnosis_id,
        "session_id": session_id,
        "user_message_id": user_message_id,
        "assistant_message_id": assistant_message_id,
        "context_snapshot_id": snapshot_id,
        "retrieval_query_id": query_id,
        "model_call_id": model_call_id,
        "conclusion_id": conclusion_id,
    }


def list_agent_sessions(limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT s.*,
                      (SELECT COUNT(*) FROM agent_messages m
                       WHERE m.session_id=s.session_id) AS message_count,
                      (SELECT COUNT(*) FROM diagnosis_conclusions c
                       WHERE c.session_id=s.session_id) AS conclusion_count
               FROM agent_sessions s
               ORDER BY s.updated_at_utc DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_agent_session(session_id: str) -> dict | None:
    with get_conn() as conn:
        session = conn.execute(
            "SELECT * FROM agent_sessions WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if not session:
            return None
        messages = conn.execute(
            """SELECT * FROM agent_messages
               WHERE session_id=? ORDER BY seq_no""",
            (session_id,),
        ).fetchall()
        contexts = conn.execute(
            """SELECT * FROM context_snapshots
               WHERE session_id=? ORDER BY created_at_utc""",
            (session_id,),
        ).fetchall()
        tool_calls = conn.execute(
            """SELECT tc.*, tr.tool_name, tr.transport
               FROM tool_calls tc
               JOIN tool_registry tr ON tr.tool_id=tc.tool_id
               WHERE tc.session_id=? ORDER BY tc.started_at_utc""",
            (session_id,),
        ).fetchall()
        tool_results = conn.execute(
            """SELECT tcr.*, tc.session_id, tr.tool_name
               FROM tool_call_results tcr
               JOIN tool_calls tc ON tc.tool_call_id=tcr.tool_call_id
               JOIN tool_registry tr ON tr.tool_id=tc.tool_id
               WHERE tc.session_id=? ORDER BY tcr.created_at_utc""",
            (session_id,),
        ).fetchall()
        model_calls = conn.execute(
            """SELECT * FROM model_calls
               WHERE session_id=? ORDER BY created_at_utc""",
            (session_id,),
        ).fetchall()
        conclusions = conn.execute(
            """SELECT * FROM diagnosis_conclusions
               WHERE session_id=? ORDER BY created_at_utc""",
            (session_id,),
        ).fetchall()
    return {
        "session": dict(session),
        "messages": [dict(row) for row in messages],
        "context_snapshots": [dict(row) for row in contexts],
        "tool_calls": [dict(row) for row in tool_calls],
        "tool_results": [dict(row) for row in tool_results],
        "model_calls": [dict(row) for row in model_calls],
        "conclusions": [dict(row) for row in conclusions],
    }


def get_diagnosis_detail(diagnosis_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM diagnosis_records WHERE id=?",
            (diagnosis_id,),
        ).fetchone()
        if not row:
            return None
        record = dict(row)
        retrieved_docs = []
        if record.get("retrieval_query_id"):
            hits = conn.execute(
                """SELECT * FROM retrieval_hits
                   WHERE query_id=? ORDER BY rank_no""",
                (record["retrieval_query_id"],),
            ).fetchall()
            retrieved_docs = [
                {
                    "id": hit["doc_id"],
                    "hit_id": hit["hit_id"],
                    "chunk_id": hit["chunk_id"],
                    "rank": hit["rank_no"],
                    "distance": hit["distance"],
                    "title": hit["title"],
                    "category": hit["category"],
                    "applicable_device": hit["applicable_device"],
                    "version": hit["version"],
                    "source": hit["source"],
                    "content": hit["content"],
                    "content_hash": hit["content_hash"],
                }
                for hit in hits
            ]
        conclusion = None
        evidence_links = []
        changes = []
        if record.get("conclusion_id"):
            conclusion_row = conn.execute(
                """SELECT * FROM diagnosis_conclusions
                   WHERE conclusion_id=?""",
                (record["conclusion_id"],),
            ).fetchone()
            conclusion = dict(conclusion_row) if conclusion_row else None
            link_rows = conn.execute(
                """SELECT * FROM evidence_links
                   WHERE conclusion_id=? ORDER BY created_at_utc, link_id""",
                (record["conclusion_id"],),
            ).fetchall()
            evidence_links = [dict(link) for link in link_rows]
        if record.get("id"):
            change_rows = conn.execute(
                """SELECT * FROM change_requests
                   WHERE diagnosis_id=? OR conclusion_id=?
                   ORDER BY created_at_utc DESC""",
                (record["id"], record.get("conclusion_id")),
            ).fetchall()
            changes = [dict(change) for change in change_rows]

    session = get_agent_session(record["session_id"]) if record.get("session_id") else None
    return {
        "diagnosis": record,
        "retrieved_docs": retrieved_docs,
        "conclusion": conclusion,
        "evidence_links": evidence_links,
        "session": session,
        "changes": changes,
    }


def list_tools(enabled_only: bool = True) -> list[dict]:
    with get_conn() as conn:
        sql = "SELECT * FROM tool_registry"
        if enabled_only:
            sql += " WHERE enabled=1"
        rows = conn.execute(sql + " ORDER BY tool_name").fetchall()
    tools = []
    for row in rows:
        item = dict(row)
        item["input_schema"] = _json_value(item.pop("input_schema_json")) or {}
        tools.append(item)
    return tools


def get_tool(tool_name: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM tool_registry WHERE tool_name=?",
            (tool_name,),
        ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["input_schema"] = _json_value(item.pop("input_schema_json")) or {}
    return item


def start_tool_call(
    tool_name: str,
    arguments: dict | None = None,
    session_id: str | None = None,
    message_id: str | None = None,
) -> dict:
    init_db()
    tool = get_tool(tool_name)
    if not tool:
        raise ValueError(f"工具未注册: {tool_name}")
    if not tool["enabled"]:
        raise ValueError(f"工具已禁用: {tool_name}")
    if not tool["read_only"]:
        raise ValueError(f"第二期只允许调用只读工具: {tool_name}")
    timestamp = now_iso()
    tool_call_id = _new_id("toolcall")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO tool_calls
               (tool_call_id, session_id, message_id, tool_id, arguments_json,
                status, error_code, error_message, started_at_utc,
                finished_at_utc, created_at_utc)
               VALUES (?, ?, ?, ?, ?, 'running', NULL, NULL, ?, NULL, ?)""",
            (
                tool_call_id,
                session_id,
                message_id,
                tool["tool_id"],
                json.dumps(arguments or {}, ensure_ascii=False),
                timestamp,
                timestamp,
            ),
        )
    return {
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "tool": tool,
        "arguments": arguments or {},
        "started_at_utc": timestamp,
    }


def finish_tool_call(
    tool_call_id: str,
    *,
    status: str,
    result: Any = None,
    result_kind: str = "json",
    collection_run_id: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict:
    timestamp = now_iso()
    result_text = json.dumps(result, ensure_ascii=False) if result is not None else "{}"
    with get_conn() as conn:
        call = conn.execute(
            "SELECT * FROM tool_calls WHERE tool_call_id=?",
            (tool_call_id,),
        ).fetchone()
        if not call:
            raise ValueError(f"工具调用不存在: {tool_call_id}")
        conn.execute(
            """UPDATE tool_calls
               SET status=?, error_code=?, error_message=?, finished_at_utc=?
               WHERE tool_call_id=?""",
            (status, error_code, error_message, timestamp, tool_call_id),
        )
        result_id = _new_id("toolresult")
        blob_id = _store_blob_conn(conn, result_text, "application/json")
        conn.execute(
            """INSERT INTO tool_call_results
               (result_id, tool_call_id, result_kind, collection_run_id,
                result_json, blob_id, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                result_id,
                tool_call_id,
                result_kind,
                collection_run_id,
                result_text,
                blob_id,
                timestamp,
            ),
        )
    return {
        "tool_call_id": tool_call_id,
        "result_id": result_id,
        "collection_run_id": collection_run_id,
        "status": status,
        "result": result,
        "finished_at_utc": timestamp,
    }


def get_tool_call(tool_call_id: str) -> dict | None:
    with get_conn() as conn:
        call = conn.execute(
            """SELECT tc.*, tr.tool_name, tr.transport, tr.read_only
               FROM tool_calls tc
               JOIN tool_registry tr ON tr.tool_id=tc.tool_id
               WHERE tc.tool_call_id=?""",
            (tool_call_id,),
        ).fetchone()
        if not call:
            return None
        result = conn.execute(
            """SELECT * FROM tool_call_results
               WHERE tool_call_id=?""",
            (tool_call_id,),
        ).fetchone()
    payload = dict(call)
    payload["arguments"] = _json_value(payload.pop("arguments_json")) or {}
    payload["result"] = dict(result) if result else None
    if payload["result"]:
        payload["result"]["result"] = (
            _json_value(payload["result"].pop("result_json")) or {}
        )
    return payload


def _change_event_conn(
    conn: sqlite3.Connection,
    change_id: str,
    event_type: str,
    actor: str,
    from_status: str | None = None,
    to_status: str | None = None,
    payload: Any = None,
) -> str:
    payload_text = _json_text(payload) if payload is not None else None
    event_id = _new_id("changeevent")
    conn.execute(
        """INSERT INTO change_events
           (event_id, change_id, event_type, actor, from_status, to_status,
            payload_json, payload_hash, created_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event_id,
            change_id,
            event_type,
            actor,
            from_status,
            to_status,
            payload_text,
            _sha256(payload_text or f"{change_id}:{event_type}:{actor}"),
            now_iso(),
        ),
    )
    return event_id


def record_change_event(
    change_id: str,
    event_type: str,
    actor: str,
    *,
    payload: Any = None,
    to_status: str | None = None,
) -> str:
    """追加一条变更事件，供业务层的失败或人工备注使用。"""
    with get_conn() as conn:
        change = _get_change_row_conn(conn, change_id)
        return _change_event_conn(
            conn,
            change_id,
            event_type,
            actor,
            change["status"],
            to_status,
            payload,
        )


def _get_change_row_conn(conn: sqlite3.Connection, change_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM change_requests WHERE change_id=?",
        (change_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"变更请求不存在: {change_id}")
    return row


def _require_verified_backup_conn(conn: sqlite3.Connection, change_id: str) -> None:
    row = conn.execute(
        """SELECT 1 FROM config_backups
           WHERE change_id=? AND backup_type='before' AND verified=1
           LIMIT 1""",
        (change_id,),
    ).fetchone()
    if not row:
        raise ValueError("执行变更前必须先完成并校验一次配置备份")


def create_change_request(
    *,
    device_name: str,
    change_type: str = "software",
    title: str,
    description: str | None = None,
    risk_level: str = "medium",
    operator: str = "web",
    diagnosis_id: int | None = None,
    conclusion_id: str | None = None,
    session_id: str | None = None,
    rollback_plan: str | None = None,
    planned_at_utc: str | None = None,
    status: str = "draft",
) -> dict:
    """创建人工变更请求。变更本身不会自动执行。"""
    init_db()
    if change_type not in ("physical", "software", "mixed"):
        raise ValueError("change_type 必须是 physical、software 或 mixed")
    if risk_level not in ("low", "medium", "high"):
        raise ValueError("risk_level 必须是 low、medium 或 high")
    if status not in (
        "draft",
        "pending_approval",
        "approved",
        "rejected",
        "cancelled",
    ):
        raise ValueError("新建变更的 status 必须是 draft、pending_approval、approved、rejected 或 cancelled")
    if not title.strip():
        raise ValueError("变更标题不能为空")

    with get_conn() as conn:
        device = _resolve_device_conn(conn, device_name)
        if not device:
            raise ValueError(f"设备不存在: {device_name}")
        if diagnosis_id and not conclusion_id:
            diagnosis = conn.execute(
                "SELECT * FROM diagnosis_records WHERE id=?",
                (diagnosis_id,),
            ).fetchone()
            if diagnosis:
                conclusion_id = diagnosis["conclusion_id"]
                session_id = session_id or diagnosis["session_id"]
        if conclusion_id and not session_id:
            conclusion = conn.execute(
                "SELECT * FROM diagnosis_conclusions WHERE conclusion_id=?",
                (conclusion_id,),
            ).fetchone()
            if conclusion:
                session_id = conclusion["session_id"]
        change_id = _new_id("change")
        timestamp = now_iso()
        conn.execute(
            """INSERT INTO change_requests
               (change_id, conclusion_id, diagnosis_id, session_id, device_id,
                device_name, change_type, title, description, risk_level, status,
                requested_by, approved_by, planned_at_utc, started_at_utc,
                finished_at_utc, rollback_plan, legacy_action_id,
                created_at_utc, updated_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, NULL,
                       ?, NULL, ?, ?)""",
            (
                change_id,
                conclusion_id,
                diagnosis_id,
                session_id,
                device["device_id"],
                device["device_name"],
                change_type,
                title.strip(),
                description,
                risk_level,
                status,
                operator,
                planned_at_utc,
                rollback_plan,
                timestamp,
                timestamp,
            ),
        )
        _change_event_conn(
            conn,
            change_id,
            "created",
            operator,
            None,
            status,
            {
                "device_name": device["device_name"],
                "change_type": change_type,
                "title": title.strip(),
            },
        )
    return {
        "change_id": change_id,
        "device_id": device["device_id"],
        "device_name": device["device_name"],
        "status": status,
    }


def list_changes(limit: int = 50, status: str | None = None) -> list[dict]:
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                """SELECT c.*, d.site_id, s.name AS site
                   FROM change_requests c
                   LEFT JOIN devices d ON d.device_id=c.device_id
                   LEFT JOIN sites s ON s.site_id=d.site_id
                   WHERE c.status=? ORDER BY c.updated_at_utc DESC LIMIT ?""",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT c.*, d.site_id, s.name AS site
                   FROM change_requests c
                   LEFT JOIN devices d ON d.device_id=c.device_id
                   LEFT JOIN sites s ON s.site_id=d.site_id
                   ORDER BY c.updated_at_utc DESC LIMIT ?""",
                (limit,),
            ).fetchall()
    return [dict(row) for row in rows]


def get_change(change_id: str) -> dict | None:
    with get_conn() as conn:
        change = conn.execute(
            """SELECT c.*, d.device_key, d.mgmt_ip, d.device_type,
                      s.name AS site
               FROM change_requests c
               LEFT JOIN devices d ON d.device_id=c.device_id
               LEFT JOIN sites s ON s.site_id=d.site_id
               WHERE c.change_id=?""",
            (change_id,),
        ).fetchone()
        if not change:
            return None
        steps = conn.execute(
            "SELECT * FROM change_steps WHERE change_id=? ORDER BY seq_no",
            (change_id,),
        ).fetchall()
        approvals = conn.execute(
            """SELECT * FROM change_approvals
               WHERE change_id=? ORDER BY created_at_utc""",
            (change_id,),
        ).fetchall()
        events = conn.execute(
            """SELECT * FROM change_events
               WHERE change_id=? ORDER BY created_at_utc, rowid""",
            (change_id,),
        ).fetchall()
        executions = conn.execute(
            """SELECT ce.*, rb.content_text AS stdout_text,
                      reb.content_text AS stderr_text
               FROM command_executions ce
               LEFT JOIN raw_blobs rb ON rb.blob_id=ce.stdout_blob_id
               LEFT JOIN raw_blobs reb ON reb.blob_id=ce.stderr_blob_id
               JOIN change_steps cs ON cs.step_id=ce.step_id
               WHERE cs.change_id=? ORDER BY ce.created_at_utc""",
            (change_id,),
        ).fetchall()
        physical = conn.execute(
            """SELECT pa.*, rb.content_text AS evidence_text
               FROM physical_actions pa
               LEFT JOIN raw_blobs rb ON rb.blob_id=pa.evidence_blob_id
               JOIN change_steps cs ON cs.step_id=pa.step_id
               WHERE cs.change_id=? ORDER BY pa.performed_at_utc""",
            (change_id,),
        ).fetchall()
        backups = conn.execute(
            "SELECT * FROM config_backups WHERE change_id=? ORDER BY captured_at_utc",
            (change_id,),
        ).fetchall()
        rechecks = conn.execute(
            "SELECT * FROM rechecks WHERE change_id=? ORDER BY executed_at_utc",
            (change_id,),
        ).fetchall()
        attachments = conn.execute(
            """SELECT * FROM attachments
               WHERE (owner_type='change' AND owner_id=?)
                  OR (owner_type='step' AND owner_id IN
                      (SELECT step_id FROM change_steps WHERE change_id=?))
                  OR (owner_type='physical_action' AND owner_id IN
                      (SELECT action_id FROM physical_actions pa
                       JOIN change_steps cs ON cs.step_id=pa.step_id
                       WHERE cs.change_id=?))
               ORDER BY created_at_utc""",
            (change_id, change_id, change_id),
        ).fetchall()
        conclusion = None
        if change["conclusion_id"]:
            conclusion_row = conn.execute(
                "SELECT * FROM diagnosis_conclusions WHERE conclusion_id=?",
                (change["conclusion_id"],),
            ).fetchone()
            conclusion = dict(conclusion_row) if conclusion_row else None
    return {
        "change": dict(change),
        "steps": [dict(row) for row in steps],
        "approvals": [dict(row) for row in approvals],
        "events": [
            {**dict(row), "payload": _json_value(row["payload_json"]) or {}}
            for row in events
        ],
        "command_executions": [dict(row) for row in executions],
        "physical_actions": [dict(row) for row in physical],
        "config_backups": [dict(row) for row in backups],
        "rechecks": [
            {
                **dict(row),
                "expected": _json_value(row["expected_json"]) or {},
                "actual": _json_value(row["actual_json"]) or {},
            }
            for row in rechecks
        ],
        "attachments": [dict(row) for row in attachments],
        "conclusion": conclusion,
    }


def list_changes_for_diagnosis(diagnosis_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM change_requests
               WHERE diagnosis_id=? ORDER BY created_at_utc DESC""",
            (diagnosis_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def add_change_step(
    change_id: str,
    *,
    step_type: str,
    title: str,
    source_type: str = "human",
    command_text: str | None = None,
    expected_result: str | None = None,
    requires_approval: bool = False,
    operator: str = "web",
) -> dict:
    init_db()
    if step_type not in ("physical", "software", "verification"):
        raise ValueError("step_type 必须是 physical、software 或 verification")
    if source_type not in ("llm", "human"):
        raise ValueError("source_type 必须是 llm 或 human")
    with get_conn() as conn:
        change = _get_change_row_conn(conn, change_id)
        if change["status"] in ("completed", "cancelled", "rejected", "rolled_back"):
            raise ValueError(f"当前状态 {change['status']} 不允许新增步骤")
        seq_row = conn.execute(
            "SELECT COALESCE(MAX(seq_no), 0) + 1 FROM change_steps WHERE change_id=?",
            (change_id,),
        ).fetchone()
        seq_no = int(seq_row[0])
        step_id = _new_id("step")
        timestamp = now_iso()
        conn.execute(
            """INSERT INTO change_steps
               (step_id, change_id, seq_no, step_type, source_type, title,
                command_text, expected_result, requires_approval, status,
                executed_by, started_at_utc, finished_at_utc, result_summary,
                created_at_utc, updated_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, NULL,
                       NULL, ?, ?)""",
            (
                step_id,
                change_id,
                seq_no,
                step_type,
                source_type,
                title.strip(),
                command_text,
                expected_result,
                1 if requires_approval else 0,
                timestamp,
                timestamp,
            ),
        )
        conn.execute(
            "UPDATE change_requests SET updated_at_utc=? WHERE change_id=?",
            (timestamp, change_id),
        )
        _change_event_conn(
            conn,
            change_id,
            "step_added",
            operator,
            change["status"],
            change["status"],
            {"step_id": step_id, "step_type": step_type, "source_type": source_type},
        )
    return {"step_id": step_id, "seq_no": seq_no, "status": "pending"}


def approve_change(
    change_id: str,
    *,
    approver: str,
    decision: str = "approved",
    comment: str | None = None,
) -> dict:
    if decision not in ("approved", "rejected", "changes_requested"):
        raise ValueError("decision 必须是 approved、rejected 或 changes_requested")
    with get_conn() as conn:
        change = _get_change_row_conn(conn, change_id)
        if change["status"] in ("completed", "cancelled", "rolled_back"):
            raise ValueError(f"当前状态 {change['status']} 不允许审批")
        approval_id = _new_id("approval")
        timestamp = now_iso()
        conn.execute(
            """INSERT INTO change_approvals
               (approval_id, change_id, approver, decision, comment, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (approval_id, change_id, approver, decision, comment, timestamp),
        )
        target_status = {
            "approved": "approved",
            "rejected": "rejected",
            "changes_requested": "pending_approval",
        }[decision]
        conn.execute(
            """UPDATE change_requests
               SET status=?, approved_by=?, updated_at_utc=? WHERE change_id=?""",
            (
                target_status,
                approver if decision == "approved" else change["approved_by"],
                timestamp,
                change_id,
            ),
        )
        _change_event_conn(
            conn,
            change_id,
            "approval_recorded",
            approver,
            change["status"],
            target_status,
            {"decision": decision, "comment": comment, "approval_id": approval_id},
        )
    return {"change_id": change_id, "approval_id": approval_id, "status": target_status}


def start_change(change_id: str, *, operator: str) -> dict:
    with get_conn() as conn:
        change = _get_change_row_conn(conn, change_id)
        if change["status"] not in ("approved", "executing"):
            raise ValueError("只有已审批或执行中的变更可以开始执行")
        timestamp = now_iso()
        conn.execute(
            """UPDATE change_requests
               SET status='executing', started_at_utc=COALESCE(started_at_utc, ?),
                   updated_at_utc=? WHERE change_id=?""",
            (timestamp, timestamp, change_id),
        )
        _change_event_conn(
            conn, change_id, "execution_started", operator,
            change["status"], "executing", None,
        )
    return {"change_id": change_id, "status": "executing", "started_at_utc": timestamp}


def record_command_execution(
    change_id: str,
    step_id: str,
    *,
    command_text: str,
    output: str | None,
    stderr: str | None = None,
    exit_code: int | None = 0,
    operator: str = "web",
    execution_mode: str = "manual",
    result_summary: str | None = None,
    started_at_utc: str | None = None,
    finished_at_utc: str | None = None,
) -> dict:
    if execution_mode not in ("manual", "readonly", "approved_automation"):
        raise ValueError("execution_mode 不合法")
    with get_conn() as conn:
        change = _get_change_row_conn(conn, change_id)
        if change["status"] not in ("approved", "executing"):
            raise ValueError("只有已审批或执行中的变更可以记录命令执行")
        _require_verified_backup_conn(conn, change_id)
        step = conn.execute(
            "SELECT * FROM change_steps WHERE step_id=? AND change_id=?",
            (step_id, change_id),
        ).fetchone()
        if not step:
            raise ValueError("变更步骤不存在")
        if step["step_type"] == "physical":
            raise ValueError("物理步骤请使用物理操作记录接口")
        timestamp = now_iso()
        start = started_at_utc or timestamp
        finish = finished_at_utc or timestamp
        stdout_blob_id = _store_blob_conn(conn, output, "text/plain")
        stderr_blob_id = _store_blob_conn(conn, stderr, "text/plain")
        execution_id = _new_id("execution")
        conn.execute(
            """INSERT INTO command_executions
               (execution_id, step_id, device_id, command_text, execution_mode,
                executed_by, started_at_utc, finished_at_utc, exit_code,
                stdout_blob_id, stderr_blob_id, result_summary, approval_id,
                created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)""",
            (
                execution_id,
                step_id,
                change["device_id"],
                command_text,
                execution_mode,
                operator,
                start,
                finish,
                exit_code,
                stdout_blob_id,
                stderr_blob_id,
                result_summary,
                timestamp,
            ),
        )
        step_status = "success" if exit_code == 0 else "failed"
        conn.execute(
            """UPDATE change_steps
               SET status=?, executed_by=?, started_at_utc=?, finished_at_utc=?,
                   result_summary=?, updated_at_utc=? WHERE step_id=?""",
            (
                step_status,
                operator,
                start,
                finish,
                result_summary,
                timestamp,
                step_id,
            ),
        )
        conn.execute(
            """UPDATE change_requests
               SET status=CASE WHEN status='approved' THEN 'executing' ELSE status END,
                   started_at_utc=COALESCE(started_at_utc, ?), updated_at_utc=?
               WHERE change_id=?""",
            (start, timestamp, change_id),
        )
        _change_event_conn(
            conn,
            change_id,
            "command_executed",
            operator,
            change["status"],
            "executing" if change["status"] == "approved" else change["status"],
            {
                "execution_id": execution_id,
                "step_id": step_id,
                "command_text": command_text,
                "exit_code": exit_code,
            },
        )
    return {
        "execution_id": execution_id,
        "step_id": step_id,
        "step_status": step_status,
        "exit_code": exit_code,
    }


def record_physical_action(
    change_id: str,
    step_id: str,
    *,
    action_type: str,
    description: str,
    operator: str = "web",
    result: str | None = None,
    evidence_text: str | None = None,
    performed_at_utc: str | None = None,
) -> dict:
    if action_type not in ("cable", "power", "console", "module", "other"):
        raise ValueError("action_type 不合法")
    with get_conn() as conn:
        change = _get_change_row_conn(conn, change_id)
        if change["status"] not in ("approved", "executing"):
            raise ValueError("只有已审批或执行中的变更可以记录物理操作")
        _require_verified_backup_conn(conn, change_id)
        step = conn.execute(
            "SELECT * FROM change_steps WHERE step_id=? AND change_id=?",
            (step_id, change_id),
        ).fetchone()
        if not step:
            raise ValueError("变更步骤不存在")
        if step["step_type"] not in ("physical", "verification"):
            raise ValueError("当前步骤不是物理操作步骤")
        timestamp = now_iso()
        performed = performed_at_utc or timestamp
        evidence_blob_id = _store_blob_conn(conn, evidence_text, "text/plain")
        action_id = _new_id("physical")
        conn.execute(
            """INSERT INTO physical_actions
               (action_id, step_id, device_id, action_type, description,
                performed_by, performed_at_utc, evidence_blob_id, result,
                created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                action_id,
                step_id,
                change["device_id"],
                action_type,
                description,
                operator,
                performed,
                evidence_blob_id,
                result,
                timestamp,
            ),
        )
        conn.execute(
            """UPDATE change_steps
               SET status='success', executed_by=?, started_at_utc=?,
                   finished_at_utc=?, result_summary=?, updated_at_utc=?
               WHERE step_id=?""",
            (operator, performed, performed, result or description, timestamp, step_id),
        )
        conn.execute(
            """UPDATE change_requests
               SET status=CASE WHEN status='approved' THEN 'executing' ELSE status END,
                   started_at_utc=COALESCE(started_at_utc, ?), updated_at_utc=?
               WHERE change_id=?""",
            (performed, timestamp, change_id),
        )
        _change_event_conn(
            conn,
            change_id,
            "physical_action_recorded",
            operator,
            change["status"],
            "executing" if change["status"] == "approved" else change["status"],
            {
                "action_id": action_id,
                "step_id": step_id,
                "action_type": action_type,
                "description": description,
            },
        )
    return {"action_id": action_id, "step_id": step_id, "status": "success"}


def record_config_backup(
    change_id: str,
    *,
    content: str,
    storage_ref: str | None,
    sha256: str | None,
    operator: str = "web",
    backup_type: str = "before",
    verified: bool = True,
) -> dict:
    if backup_type not in ("before", "after", "rollback"):
        raise ValueError("backup_type 不合法")
    with get_conn() as conn:
        change = _get_change_row_conn(conn, change_id)
        if change["status"] not in ("approved", "executing"):
            raise ValueError("只有已审批或执行中的变更可以记录配置备份")
        blob_id = _store_blob_conn(conn, content, "text/plain")
        digest = sha256 or _sha256(content)
        backup_id = _new_id("backup")
        timestamp = now_iso()
        conn.execute(
            """INSERT INTO config_backups
               (backup_id, change_id, device_id, config_snapshot_id, blob_id,
                sha256, captured_at_utc, backup_type, verified, storage_ref,
                restore_ref, created_at_utc)
               VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, NULL, ?)""",
            (
                backup_id,
                change_id,
                change["device_id"],
                blob_id,
                digest,
                timestamp,
                backup_type,
                1 if verified else 0,
                storage_ref,
                timestamp,
            ),
        )
        _change_event_conn(
            conn,
            change_id,
            "config_backup_recorded",
            operator,
            change["status"],
            change["status"],
            {
                "backup_id": backup_id,
                "sha256": digest,
                "storage_ref": storage_ref,
                "backup_type": backup_type,
            },
        )
    return {"backup_id": backup_id, "sha256": digest, "storage_ref": storage_ref}


def record_recheck(
    change_id: str,
    *,
    collection_run_id: str | None,
    source_device_id: str | None,
    source_device_name: str | None,
    target: str,
    check_type: str,
    expected: dict | None,
    actual: dict | None,
    pass_fail: str,
    evidence_observation_id: str | None = None,
    operator: str = "web",
    executed_at_utc: str | None = None,
) -> dict:
    if pass_fail not in ("pass", "fail", "inconclusive"):
        raise ValueError("pass_fail 必须是 pass、fail 或 inconclusive")
    if check_type not in ("ping", "interface", "route", "acl", "application"):
        raise ValueError("check_type 不合法")
    with get_conn() as conn:
        change = _get_change_row_conn(conn, change_id)
        if change["status"] not in ("approved", "executing"):
            raise ValueError("只有已审批或执行中的变更可以记录复测")
        _require_verified_backup_conn(conn, change_id)
        timestamp = executed_at_utc or now_iso()
        recheck_id = _new_id("recheck")
        conn.execute(
            """INSERT INTO rechecks
               (recheck_id, change_id, collection_run_id, source_device_id,
                source_device_name, target, check_type, expected_json,
                actual_json, pass_fail, evidence_observation_id,
                executed_at_utc, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                recheck_id,
                change_id,
                collection_run_id,
                source_device_id,
                source_device_name,
                target,
                check_type,
                _json_text(expected),
                _json_text(actual),
                pass_fail,
                evidence_observation_id,
                timestamp,
                timestamp,
            ),
        )
        conn.execute(
            "UPDATE change_requests SET updated_at_utc=? WHERE change_id=?",
            (timestamp, change_id),
        )
        _change_event_conn(
            conn,
            change_id,
            "recheck_recorded",
            operator,
            change["status"],
            change["status"],
            {
                "recheck_id": recheck_id,
                "pass_fail": pass_fail,
                "target": target,
                "collection_run_id": collection_run_id,
            },
        )
    return {"recheck_id": recheck_id, "pass_fail": pass_fail}


def complete_change(
    change_id: str,
    *,
    operator: str,
    outcome: str = "completed",
) -> dict:
    if outcome not in ("completed", "failed", "rolled_back"):
        raise ValueError("outcome 不合法")
    with get_conn() as conn:
        change = _get_change_row_conn(conn, change_id)
        timestamp = now_iso()
        conn.execute(
            """UPDATE change_requests
               SET status=?, finished_at_utc=?, updated_at_utc=?
               WHERE change_id=?""",
            (outcome, timestamp, timestamp, change_id),
        )
        _change_event_conn(
            conn, change_id, "change_finished", operator,
            change["status"], outcome, {"outcome": outcome},
        )
    return {"change_id": change_id, "status": outcome, "finished_at_utc": timestamp}


def record_health_check(component: str, status: str, details: dict | None = None) -> str:
    if status not in ("ready", "degraded", "error", "unknown"):
        raise ValueError("status 不合法")
    check_id = _new_id("health")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO health_checks
               (check_id, component, status, details_json, checked_at_utc)
               VALUES (?, ?, ?, ?, ?)""",
            (check_id, component, status, _json_text(details), now_iso()),
        )
    return check_id


def list_health_checks(limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM health_checks ORDER BY checked_at_utc DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def sync_knowledge_catalog(
    articles: list[dict],
    *,
    retrieved_at_utc: str | None = None,
) -> dict[str, dict]:
    """把文章、来源和版本写入三期知识目录，返回 doc_id 元数据。"""
    init_db()
    with get_conn() as conn:
        return _sync_knowledge_catalog_conn(
            conn,
            articles,
            retrieved_at_utc=retrieved_at_utc,
        )


def begin_knowledge_index(
    *,
    index_name: str,
    collection_name: str,
    model_name: str,
    runtime: str = "fastembed-onnx",
    dimension: int | None = None,
    cache_ref: str | None = None,
    chroma_path: str | None = None,
    content_hash: str | None = None,
) -> str:
    """创建一次索引构建记录，成功后再由 finish_knowledge_index 发布。"""
    init_db()
    timestamp = now_iso()
    model_id = _stable_id("embmodel", f"{runtime}:{model_name}")
    index_version_id = _new_id("kbindex")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO embedding_models
               (model_id, model_name, dimension, runtime, cache_ref, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(model_name, runtime) DO UPDATE SET
                 dimension=COALESCE(excluded.dimension, embedding_models.dimension),
                 cache_ref=COALESCE(excluded.cache_ref, embedding_models.cache_ref)""",
            (model_id, model_name, dimension, runtime, cache_ref, timestamp),
        )
        conn.execute(
            """INSERT INTO knowledge_index_versions
               (index_version_id, index_name, collection_name, embedding_model_id,
                chroma_path, article_count, content_hash, status, is_active,
                error_message, built_at_utc, created_at_utc)
               VALUES (?, ?, ?, ?, ?, 0, ?, 'building', 0, NULL, NULL, ?)""",
            (
                index_version_id,
                index_name,
                collection_name,
                model_id,
                chroma_path,
                content_hash,
                timestamp,
            ),
        )
    return index_version_id


def finish_knowledge_index(
    index_version_id: str,
    *,
    status: str,
    article_count: int = 0,
    content_hash: str | None = None,
    error_message: str | None = None,
) -> dict:
    """发布或标记一次知识库索引构建。"""
    if status not in ("ready", "failed", "retired"):
        raise ValueError("status 必须是 ready、failed 或 retired")
    init_db()
    timestamp = now_iso()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM knowledge_index_versions WHERE index_version_id=?",
            (index_version_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"知识库索引不存在: {index_version_id}")
        index_name = row["index_name"]
        conn.execute(
            """UPDATE knowledge_index_versions
               SET status=?, article_count=?, content_hash=COALESCE(?, content_hash),
                   error_message=?, built_at_utc=?, is_active=?
               WHERE index_version_id=?""",
            (
                status,
                article_count,
                content_hash,
                error_message,
                timestamp if status in ("ready", "failed") else row["built_at_utc"],
                1 if status == "ready" else 0,
                index_version_id,
            ),
        )
        if status == "ready":
            conn.execute(
                """UPDATE knowledge_index_versions
                   SET is_active=0,
                       status=CASE WHEN status='ready' THEN 'retired' ELSE status END
                   WHERE index_name=? AND index_version_id<>?""",
                (index_name, index_version_id),
            )
            conn.execute(
                "UPDATE kb_chunks SET is_active=0 WHERE index_name=?",
                (index_name,),
            )
            conn.execute(
                """UPDATE kb_chunks
                   SET index_version_id=?, is_active=1
                   WHERE index_name=?
                     AND article_version_id IN (
                         SELECT article_version_id FROM knowledge_article_versions
                         WHERE status='active'
                     )""",
                (index_version_id, index_name),
            )
    return {
        "index_version_id": index_version_id,
        "index_name": index_name,
        "status": status,
        "article_count": article_count,
        "content_hash": content_hash,
        "error_message": error_message,
    }


def list_knowledge_indexes(
    index_name: str | None = None,
    limit: int = 20,
) -> list[dict]:
    with get_conn() as conn:
        sql = """SELECT kiv.*, em.model_name, em.dimension, em.runtime,
                        em.cache_ref
                 FROM knowledge_index_versions kiv
                 LEFT JOIN embedding_models em
                   ON em.model_id=kiv.embedding_model_id"""
        params: list[Any] = []
        if index_name:
            sql += " WHERE kiv.index_name=?"
            params.append(index_name)
        sql += " ORDER BY kiv.created_at_utc DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def get_active_knowledge_index(index_name: str = "ops_knowledge_base") -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT kiv.*, em.model_name, em.dimension, em.runtime
               FROM knowledge_index_versions kiv
               LEFT JOIN embedding_models em
                 ON em.model_id=kiv.embedding_model_id
               WHERE kiv.index_name=? AND kiv.is_active=1
               ORDER BY kiv.created_at_utc DESC LIMIT 1""",
            (index_name,),
        ).fetchone()
    return dict(row) if row else None


def knowledge_catalog_summary() -> dict:
    with get_conn() as conn:
        counts = {
            "sources": conn.execute("SELECT COUNT(*) FROM knowledge_sources").fetchone()[0],
            "articles": conn.execute("SELECT COUNT(*) FROM knowledge_articles").fetchone()[0],
            "active_versions": conn.execute(
                "SELECT COUNT(*) FROM knowledge_article_versions WHERE status='active'"
            ).fetchone()[0],
            "chunks": conn.execute(
                "SELECT COUNT(*) FROM kb_chunks WHERE is_active=1"
            ).fetchone()[0],
        }
    return {
        **counts,
        "active_index": get_active_knowledge_index(),
        "indexes": list_knowledge_indexes(limit=10),
    }


def user_has_permission(username: str, permission_code: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT 1
               FROM users u
               JOIN user_roles ur ON ur.user_id=u.user_id
               JOIN role_permissions rp ON rp.role_id=ur.role_id
               JOIN permissions p ON p.permission_id=rp.permission_id
               WHERE u.username=? AND u.status='active' AND p.permission_code=?
               LIMIT 1""",
            (username, permission_code),
        ).fetchone()
    return row is not None


def list_users() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT u.user_id, u.username, u.display_name, u.status,
                      GROUP_CONCAT(r.role_name) AS roles
               FROM users u
               LEFT JOIN user_roles ur ON ur.user_id=u.user_id
               LEFT JOIN roles r ON r.role_id=ur.role_id
               GROUP BY u.user_id
               ORDER BY u.username"""
        ).fetchall()
    return [dict(row) for row in rows]


def record_audit_event(
    *,
    actor: str,
    action: str,
    target_type: str,
    target_id: str | None = None,
    request_id: str | None = None,
    details: dict | None = None,
) -> str:
    audit_id = _new_id("audit")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO audit_log
               (audit_id, actor, action, target_type, target_id, request_id,
                details_json, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                audit_id,
                actor,
                action,
                target_type,
                target_id,
                request_id,
                _json_text(details),
                now_iso(),
            ),
        )
    return audit_id


def list_audit_log(
    *,
    target_type: str | None = None,
    target_id: str | None = None,
    limit: int = 100,
) -> list[dict]:
    with get_conn() as conn:
        sql = "SELECT * FROM audit_log"
        clauses = []
        params: list[Any] = []
        if target_type:
            clauses.append("target_type=?")
            params.append(target_type)
        if target_id:
            clauses.append("target_id=?")
            params.append(target_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at_utc DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
    return [
        {
            **dict(row),
            "details": _json_value(row["details_json"]),
        }
        for row in rows
    ]


def list_retention_policies() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM retention_policies ORDER BY name"
        ).fetchall()
    policies = []
    for row in rows:
        item = dict(row)
        item["config"] = _json_value(item.pop("config_json", None))
        policies.append(item)
    return policies


def start_maintenance_run(job_type: str, *, created_by: str = "system") -> str:
    run_id = _new_id("maint")
    timestamp = now_iso()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO maintenance_runs
               (run_id, job_type, status, started_at_utc, finished_at_utc,
                summary_json, created_by, created_at_utc)
               VALUES (?, ?, 'running', ?, NULL, NULL, ?, ?)""",
            (run_id, job_type, timestamp, created_by, timestamp),
        )
    return run_id


def finish_maintenance_run(
    run_id: str,
    *,
    status: str,
    summary: dict | None = None,
) -> dict:
    if status not in ("success", "failed"):
        raise ValueError("status 必须是 success 或 failed")
    timestamp = now_iso()
    with get_conn() as conn:
        conn.execute(
            """UPDATE maintenance_runs
               SET status=?, finished_at_utc=?, summary_json=?
               WHERE run_id=?""",
            (status, timestamp, _json_text(summary), run_id),
        )
    return {"run_id": run_id, "status": status, "summary": summary}


def upsert_test_suite(
    name: str,
    *,
    description: str | None = None,
    version: str = "1",
    created_by: str = "system",
) -> str:
    suite_id = _stable_id("suite", f"{name}:{version}")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO test_suites
               (suite_id, name, description, version, created_by, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(suite_id) DO UPDATE SET
                 description=excluded.description""",
            (suite_id, name, description, version, created_by, now_iso()),
        )
    return suite_id


def upsert_test_case(
    suite_id: str,
    *,
    external_id: str,
    category: str,
    question: str,
    expected: dict,
    fixture: dict | list | None = None,
    expected_notes: str | None = None,
) -> str:
    case_id = _stable_id("case", f"{suite_id}:{external_id}")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO test_cases
               (case_id, suite_id, external_id, category, question,
                expected_json, fixture_json, expected_notes, active, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
               ON CONFLICT(suite_id, external_id) DO UPDATE SET
                 category=excluded.category,
                 question=excluded.question,
                 expected_json=excluded.expected_json,
                 fixture_json=excluded.fixture_json,
                 expected_notes=excluded.expected_notes,
                 active=1""",
            (
                case_id,
                suite_id,
                external_id,
                category,
                question,
                _json_text(expected),
                _json_text(fixture),
                expected_notes,
                now_iso(),
            ),
        )
    return case_id


def register_fault_injection(
    case_id: str,
    *,
    fault_type: str,
    injection_mode: str = "synthetic",
    device_id: str | None = None,
    description: str | None = None,
    injected_by: str = "evaluation",
    status: str = "synthetic",
) -> str:
    if injection_mode not in ("synthetic", "live"):
        raise ValueError("injection_mode 必须是 synthetic 或 live")
    if status not in ("planned", "injected", "verified", "failed", "synthetic"):
        raise ValueError("status 不合法")
    injection_id = _new_id("fault")
    timestamp = now_iso()
    with get_conn() as conn:
        if injection_mode == "synthetic":
            existing = conn.execute(
                """SELECT injection_id FROM fault_injections
                   WHERE case_id=? AND injection_mode='synthetic' AND fault_type=?
                   LIMIT 1""",
                (case_id, fault_type),
            ).fetchone()
            if existing:
                return existing[0]
        conn.execute(
            """INSERT INTO fault_injections
               (injection_id, case_id, device_id, fault_type, injection_mode,
                description, before_blob_id, after_blob_id, injected_at_utc,
                injected_by, verified_at_utc, status, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, NULL, ?, ?)""",
            (
                injection_id,
                case_id,
                device_id,
                fault_type,
                injection_mode,
                description,
                timestamp if injection_mode == "live" else None,
                injected_by,
                status,
                timestamp,
            ),
        )
    return injection_id


def start_experiment_run(
    suite_id: str,
    *,
    model_id: str,
    provider: str | None = None,
    prompt_version_id: str | None = None,
    kb_index_version_id: str | None = None,
    created_by: str = "evaluation",
) -> str:
    experiment_id = _new_id("experiment")
    timestamp = now_iso()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO experiment_runs
               (experiment_id, suite_id, model_id, provider, prompt_version_id,
                kb_index_version_id, status, started_at_utc, finished_at_utc,
                summary_json, created_by, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, 'running', ?, NULL, NULL, ?, ?)""",
            (
                experiment_id,
                suite_id,
                model_id,
                provider,
                prompt_version_id,
                kb_index_version_id,
                timestamp,
                created_by,
                timestamp,
            ),
        )
    return experiment_id


def record_experiment_result(
    experiment_id: str,
    case_id: str,
    *,
    kb_enabled: bool,
    correctness: str,
    correctness_score: float,
    executability_score: float,
    diagnosis_id: int | None = None,
    conclusion_id: str | None = None,
    evidence_run_id: str | None = None,
    evidence_hash: str | None = None,
    llm_response_time_ms: int | None = None,
    total_response_time_ms: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    answer: dict | None = None,
    evaluation: dict | None = None,
) -> str:
    if correctness not in ("correct", "partial", "wrong", "error", "skipped"):
        raise ValueError("correctness 不合法")
    result_id = _stable_id(
        "expresult", f"{experiment_id}:{case_id}:{1 if kb_enabled else 0}"
    )
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO experiment_results
               (result_id, experiment_id, case_id, kb_enabled, diagnosis_id,
                conclusion_id, evidence_run_id, evidence_hash, correctness,
                correctness_score, executability_score, llm_response_time_ms,
                total_response_time_ms, error_code, error_message,
                answer_json, evaluation_json, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(experiment_id, case_id, kb_enabled) DO UPDATE SET
                 diagnosis_id=excluded.diagnosis_id,
                 conclusion_id=excluded.conclusion_id,
                 evidence_run_id=excluded.evidence_run_id,
                 evidence_hash=excluded.evidence_hash,
                 correctness=excluded.correctness,
                 correctness_score=excluded.correctness_score,
                 executability_score=excluded.executability_score,
                 llm_response_time_ms=excluded.llm_response_time_ms,
                 total_response_time_ms=excluded.total_response_time_ms,
                 error_code=excluded.error_code,
                 error_message=excluded.error_message,
                 answer_json=excluded.answer_json,
                 evaluation_json=excluded.evaluation_json""",
            (
                result_id,
                experiment_id,
                case_id,
                1 if kb_enabled else 0,
                diagnosis_id,
                conclusion_id,
                evidence_run_id,
                evidence_hash,
                correctness,
                correctness_score,
                executability_score,
                llm_response_time_ms,
                total_response_time_ms,
                error_code,
                error_message,
                _json_text(answer or {}),
                _json_text(evaluation or {}),
                now_iso(),
            ),
        )
    return result_id


def finish_experiment_run(
    experiment_id: str,
    *,
    status: str = "completed",
    summary: dict | None = None,
) -> dict:
    if status not in ("completed", "failed", "cancelled"):
        raise ValueError("status 不合法")
    timestamp = now_iso()
    with get_conn() as conn:
        conn.execute(
            """UPDATE experiment_runs
               SET status=?, finished_at_utc=?, summary_json=?
               WHERE experiment_id=?""",
            (status, timestamp, _json_text(summary), experiment_id),
        )
    return {
        "experiment_id": experiment_id,
        "status": status,
        "finished_at_utc": timestamp,
        "summary": summary,
    }


def list_experiments(limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT er.*, ts.name AS suite_name,
                      (SELECT COUNT(*) FROM experiment_results x
                       WHERE x.experiment_id=er.experiment_id) AS result_count
               FROM experiment_runs er
               JOIN test_suites ts ON ts.suite_id=er.suite_id
               ORDER BY er.started_at_utc DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [
        {
            **dict(row),
            "summary": _json_value(row["summary_json"]),
        }
        for row in rows
    ]


def get_experiment(experiment_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT er.*, ts.name AS suite_name, ts.description AS suite_description
               FROM experiment_runs er
               JOIN test_suites ts ON ts.suite_id=er.suite_id
               WHERE er.experiment_id=?""",
            (experiment_id,),
        ).fetchone()
        if not row:
            return None
        results = conn.execute(
            """SELECT xr.*, tc.external_id, tc.category, tc.question,
                      tc.expected_json, tc.expected_notes
               FROM experiment_results xr
               JOIN test_cases tc ON tc.case_id=xr.case_id
               WHERE xr.experiment_id=?
               ORDER BY tc.external_id, xr.kb_enabled DESC""",
            (experiment_id,),
        ).fetchall()
    result_items = []
    for result in results:
        item = dict(result)
        item["expected"] = _json_value(item.pop("expected_json", None))
        item["answer"] = _json_value(item.pop("answer_json", None))
        item["evaluation"] = _json_value(item.pop("evaluation_json", None))
        result_items.append(item)
    payload = {
        **dict(row),
        "summary": _json_value(row["summary_json"]),
        "results": result_items,
    }
    return payload


def insert_manual_action(diagnosis_id, operator, action_desc, backup_ref,
                          recheck_result, status):
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO manual_actions
               (diagnosis_id, created_at, operator, action_desc, backup_ref, recheck_result, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (diagnosis_id, now_iso(), operator, action_desc, backup_ref, recheck_result, status),
        )
        return cur.lastrowid


def list_manual_actions(diagnosis_id=None):
    with get_conn() as conn:
        if diagnosis_id:
            rows = conn.execute(
                "SELECT * FROM manual_actions WHERE diagnosis_id=? ORDER BY id DESC",
                (diagnosis_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM manual_actions ORDER BY id DESC"
            ).fetchall()
    return [dict(r) for r in rows]


def set_system_status(status_key: str, status: dict):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO system_status (status_key, status_json, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(status_key) DO UPDATE SET
                 status_json=excluded.status_json,
                 updated_at=excluded.updated_at""",
            (status_key, json.dumps(status, ensure_ascii=False), now_iso()),
        )


def get_system_status(status_key: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status_json FROM system_status WHERE status_key=?",
            (status_key,),
        ).fetchone()
    return json.loads(row["status_json"]) if row else None


def list_system_status():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status_key, status_json, updated_at FROM system_status ORDER BY status_key"
        ).fetchall()
    return {
        row["status_key"]: {
            "updated_at": row["updated_at"],
            **json.loads(row["status_json"]),
        }
        for row in rows
    }
