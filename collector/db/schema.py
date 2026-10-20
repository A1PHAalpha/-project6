"""SQLite persistence package for the network operations console."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from . import _core
from ._core import (
    PROJECT_ROOT,
    _backup_before_upgrade,
    _columns,
    _ensure_column,
    _json_value,
    _legacy_tables,
    _needs_legacy_migration,
    _new_id,
    _rename_legacy_tables,
    _schema_version,
    _sha256,
    _stable_id,
    _table_exists,
    _to_utc_iso,
    _view_exists,
    now_iso,
)
from .assets import _insert_device_conn
from .collection import _insert_collection_batch_conn
from .knowledge import _sync_knowledge_catalog_conn

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

PLATFORM_SCHEMA = """
CREATE TABLE IF NOT EXISTS application_settings (
    setting_key TEXT PRIMARY KEY,
    setting_json TEXT NOT NULL,
    updated_by TEXT,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS background_jobs (
    job_id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('queued', 'running', 'success', 'partial', 'failed', 'cancelled')),
    progress INTEGER NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 100),
    message TEXT,
    request_json TEXT,
    result_json TEXT,
    error_code TEXT,
    created_by TEXT,
    created_at_utc TEXT NOT NULL,
    started_at_utc TEXT,
    finished_at_utc TEXT
);

CREATE TABLE IF NOT EXISTS terminal_sessions (
    session_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(device_id),
    device_name TEXT NOT NULL,
    operator TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'closed', 'failed')),
    backup_status TEXT NOT NULL DEFAULT 'acknowledged'
        CHECK (backup_status IN (
            'not_requested', 'acknowledged', 'completed', 'failed'
        )),
    backup_ref TEXT,
    backup_acknowledged_at_utc TEXT,
    started_at_utc TEXT NOT NULL,
    last_activity_at_utc TEXT NOT NULL,
    ended_at_utc TEXT,
    close_reason TEXT
);

CREATE TABLE IF NOT EXISTS terminal_events (
    event_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES terminal_sessions(session_id) ON DELETE CASCADE,
    seq_no INTEGER NOT NULL,
    event_type TEXT NOT NULL
        CHECK (event_type IN (
            'session_open', 'backup_notice', 'backup_result', 'command',
            'output', 'error', 'session_close'
        )),
    source TEXT NOT NULL DEFAULT 'manual'
        CHECK (source IN ('manual', 'quick_action', 'system')),
    command_text TEXT,
    output_text TEXT,
    exit_code INTEGER,
    metadata_json TEXT,
    created_at_utc TEXT NOT NULL,
    UNIQUE (session_id, seq_no)
);

CREATE INDEX IF NOT EXISTS idx_background_jobs_status
    ON background_jobs(status, created_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_terminal_sessions_device
    ON terminal_sessions(device_name, started_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_terminal_events_session
    ON terminal_events(session_id, seq_no);
"""


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
        conn,
        "kb_chunks",
        "article_version_id",
        "TEXT REFERENCES knowledge_article_versions(article_version_id)",
    )
    _ensure_column(
        conn,
        "kb_chunks",
        "source_id",
        "TEXT REFERENCES knowledge_sources(source_id)",
    )
    _ensure_column(
        conn,
        "kb_chunks",
        "index_version_id",
        "TEXT REFERENCES knowledge_index_versions(index_version_id)",
    )
    _ensure_column(conn, "kb_chunks", "applicable_platform", "TEXT")
    _ensure_column(conn, "kb_chunks", "source_url", "TEXT")
    _ensure_column(conn, "kb_chunks", "chunk_no", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(conn, "kb_chunks", "is_active", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(
        conn,
        "retrieval_hits",
        "article_version_id",
        "TEXT REFERENCES knowledge_article_versions(article_version_id)",
    )
    _ensure_column(
        conn,
        "retrieval_hits",
        "index_version_id",
        "TEXT REFERENCES knowledge_index_versions(index_version_id)",
    )
    _ensure_column(conn, "retrieval_hits", "source_url", "TEXT")
    _ensure_column(
        conn,
        "retrieval_queries",
        "index_version_id",
        "TEXT REFERENCES knowledge_index_versions(index_version_id)",
    )
    _ensure_column(
        conn,
        "diagnosis_records",
        "index_version_id",
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
        snapshot_id = row["context_snapshot_id"] or _stable_id("ctx", f"legacy-diagnosis:{diagnosis_id}")
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
        query_id = row["retrieval_query_id"] or _stable_id("retrieval", f"legacy-diagnosis:{diagnosis_id}")
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
        model_call_id = row["model_call_id"] or _stable_id("modelcall", f"legacy-diagnosis:{diagnosis_id}")
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
        conclusion_id = row["conclusion_id"] or _stable_id("conclusion", f"legacy-diagnosis:{diagnosis_id}")
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
            if (
                observation_id
                and not conn.execute(
                    "SELECT 1 FROM state_observations WHERE observation_id=?",
                    (observation_id,),
                ).fetchone()
            ):
                observation_id = None
            if (
                item_id
                and not conn.execute(
                    "SELECT 1 FROM collection_items WHERE item_id=?",
                    (item_id,),
                ).fetchone()
            ):
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
        rows = conn.execute(f'SELECT rowid, {column_sql} FROM "{table}"').fetchall()
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
    needs_time_normalization = not conn.execute("SELECT 1 FROM schema_migrations WHERE version=2").fetchone()
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
    if _schema_version(conn) < 6:
        conn.executescript(PLATFORM_SCHEMA)
        conn.execute(
            """INSERT OR IGNORE INTO schema_migrations (version, name, applied_at_utc)
               VALUES (6, 'phase4_console_platform_and_terminal_audit', ?)""",
            (now_iso(),),
        )
    _seed_kb_chunks(conn)
    _seed_tool_registry(conn)


def init_db():
    Path(_core.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_core.DB_PATH, timeout=30)
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


__all__ = (
    "BASE_SCHEMA",
    "PHASE2B_SCHEMA",
    "PHASE2_SCHEMA",
    "PHASE3_SCHEMA",
    "PLATFORM_SCHEMA",
    "SCHEMA",
    "_backfill_legacy_diagnoses",
    "_backfill_legacy_manual_actions",
    "_ensure_compatibility_view",
    "_ensure_phase2_columns",
    "_ensure_phase3_columns",
    "_legacy_change_status",
    "_migrate_legacy_collection",
    "_migrate_legacy_devices",
    "_normalize_existing_timestamps",
    "_prepare_schema",
    "_seed_command_policies",
    "_seed_kb_chunks",
    "_seed_phase3_defaults",
    "_seed_tool_registry",
    "init_db",
)
