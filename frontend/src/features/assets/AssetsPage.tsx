// Device inventory and collection workspace.
import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Database,
  Filter,
  LoaderCircle,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  X
} from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../../shared/api/client";
import {
  ConfirmDialog,
  EmptyState,
  Field,
  LoadingState,
  PageHeader,
  Pagination,
  Panel,
  SegmentedControl,
  StatusBadge
} from "../../shared/components/ui";
import { useWorkspace } from "../../shared/state/workspace";
import type { CollectionRun, CollectionSchedule, Device, Reachability, Tone } from "../../shared/types";
import { AssetRow } from "./AssetTableRow";

function runTone(status: CollectionRun["status"]): Tone {
  if (status === "success") return "success";
  if (status === "partial" || status === "running") return "warning";
  if (status === "failed") return "danger";
  return "neutral";
}

const initialDeviceForm = {
  name: "",
  role: "路由器",
  site: "总部",
  vendor: "Cisco",
  model: "IOSv",
  deviceType: "cisco_ios",
  managementIp: "",
  sshPort: 22,
  snmpPort: 161,
  interfaces: "GigabitEthernet0/0, GigabitEthernet0/1"
};

export function AssetsPage() {
  const queryClient = useQueryClient();
  const openDeviceDrawer = useWorkspace((state) => state.openDeviceDrawer);
  const [searchParams, setSearchParams] = useSearchParams();
  const initialTab = searchParams.get("tab") === "runs" ? "runs" : "devices";
  const [tab, setTab] = useState<"devices" | "runs">(initialTab);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(8);
  const [query, setQuery] = useState(searchParams.get("q") ?? "");
  const [site, setSite] = useState("");
  const [status, setStatus] = useState<Reachability | "">("");
  const [addOpen, setAddOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [form, setForm] = useState(initialDeviceForm);
  const [selected, setSelected] = useState<string[]>([]);
  const [selectionMode, setSelectionMode] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [scheduleForm, setScheduleForm] = useState<CollectionSchedule>({
    enabled: true,
    frequency_minutes: 15,
    active_window: { start: "00:00", end: "23:59" },
    scope: "all",
    concurrency: 4,
    timeout_seconds: 15,
    retries: 1,
    channels: { ssh: true, snmp: true, connectivity: true }
  });

  useEffect(() => {
    const nextTab = searchParams.get("tab") === "runs" ? "runs" : "devices";
    setTab(nextTab);
    setQuery(searchParams.get("q") ?? "");
  }, [searchParams]);

  const devices = useQuery({
    queryKey: ["devices", page, pageSize, query, site, status],
    queryFn: () => api.listDevices({ page, pageSize, q: query, site, status: status || undefined }),
    enabled: tab === "devices"
  });

  const runs = useQuery({
    queryKey: ["collection-runs", page, pageSize],
    queryFn: () => api.listCollectionRuns(page, pageSize),
    enabled: tab === "runs"
  });
  const overview = useQuery({ queryKey: ["overview"], queryFn: api.getOverview });
  const schedule = useQuery({
    queryKey: ["collection-schedule"],
    queryFn: api.getCollectionSchedule
  });

  useEffect(() => {
    if (schedule.data) setScheduleForm(schedule.data);
  }, [schedule.data]);

  const createDevice = useMutation({
    mutationFn: api.createDevice,
    onSuccess: async () => {
      setAddOpen(false);
      setForm(initialDeviceForm);
      setActionMessage("设备已写入新台账版本。");
      await queryClient.invalidateQueries({ queryKey: ["devices"] });
    }
  });

  const archiveDevices = useMutation({
    mutationFn: api.archiveDevices,
    onSuccess: async (result) => {
      setDeleteOpen(false);
      setSelectionMode(false);
      setSelected([]);
      setActionMessage(`已将 ${result.archived} 台设备标记为退役，历史证据和引用保留。`);
      await queryClient.invalidateQueries({ queryKey: ["devices"] });
      await queryClient.invalidateQueries({ queryKey: ["overview"] });
    }
  });
  const saveSchedule = useMutation({
    mutationFn: api.updateCollectionSchedule,
    onSuccess: async (value) => {
      setScheduleOpen(false);
      setActionMessage(`采集计划已保存：每 ${value.frequency_minutes} 分钟检查一次。`);
      await queryClient.invalidateQueries({ queryKey: ["collection-schedule"] });
    }
  });

  function switchTab(next: "devices" | "runs") {
    setTab(next);
    const params = new URLSearchParams(searchParams);
    if (next === "runs") params.set("tab", "runs");
    else params.delete("tab");
    setSearchParams(params, { replace: true });
  }

  function changeQuery(value: string) {
    setQuery(value);
    setPage(1);
    const params = new URLSearchParams(searchParams);
    if (value.trim()) params.set("q", value);
    else params.delete("q");
    setSearchParams(params, { replace: true });
  }

  const deviceItems = devices.data?.items ?? [];
  const currentPageNames = deviceItems.map((device) => device.name);
  const allCurrentSelected =
    currentPageNames.length > 0 && currentPageNames.every((name) => selected.includes(name));

  return (
    <div className="page-stack">
      <PageHeader
        title="设备与采集"
        description="核对设备最近一次已发布采集。管理地址不可达只标记本次采集失败，不覆盖历史状态。"
        actions={
          <>
            <button
              className={`button ${selectionMode ? "button-quiet" : "button-danger-ghost"}`}
              type="button"
              onClick={() => {
                setSelectionMode((current) => !current);
                setSelected([]);
              }}
            >
              <Trash2 size={15} />
              {selectionMode ? "取消选择" : "删除设备"}
            </button>
            <button className="button button-quiet" type="button" onClick={() => setAddOpen(true)}>
              <Plus size={15} />
              添加设备
            </button>
            <button
              className="button button-primary"
              type="button"
              onClick={async () => {
                await api.triggerCollection();
                setActionMessage("已创建立即采集任务。");
              }}
            >
              <RefreshCw size={15} />
              立即采集
            </button>
          </>
        }
      />

      {actionMessage ? (
        <div className="page-notice" role="status">
          <span>{actionMessage}</span>
          <button className="icon-button" type="button" onClick={() => setActionMessage(null)} aria-label="关闭">
            <X size={14} />
          </button>
        </div>
      ) : null}

      <div className="collection-banner">
        <span className={`status-dot ${scheduleForm.enabled ? "success" : "neutral"}`} />
        <strong>自动采集{scheduleForm.enabled ? "已开启" : "已关闭"}</strong>
        <span className="tag plain">每 {scheduleForm.frequency_minutes} 分钟</span>
        <span>
          最近一次：
          {overview.data?.latestRun
            ? `${new Date(
                overview.data.latestRun.publishedAt ?? overview.data.latestRun.startedAt
              ).toLocaleString("zh-CN")} · ${overview.data.latestRun.status}`
            : "尚无已发布批次"}
        </span>
        <div className="banner-spacer" />
        <span className="muted">启动采集 · 手动采集 · 诊断触发</span>
        <button className="button button-quiet button-xs" type="button" onClick={() => setScheduleOpen(true)}>
          采集设置
        </button>
      </div>

      <div className="asset-tabs">
        <SegmentedControl
          value={tab}
          onChange={switchTab}
          options={[
            { value: "devices", label: `设备台账 ${devices.data?.total ?? ""}` },
            { value: "runs", label: "采集批次" }
          ]}
        />
      </div>

      {tab === "devices" ? (
        <Panel className="asset-table-panel">
          <div className="table-toolbar">
            <label className="search-field">
              <Search size={15} />
              <input
                value={query}
                placeholder="搜索设备名 / IP / 型号…"
                onChange={(event) => changeQuery(event.target.value)}
              />
            </label>
            <label className="compact-select">
              <Filter size={14} />
              <select value={site} onChange={(event) => { setSite(event.target.value); setPage(1); }}>
                <option value="">全部站点</option>
                <option value="总部">总部</option>
                <option value="分支A">分支A</option>
                <option value="分支B">分支B</option>
              </select>
            </label>
            <label className="compact-select">
              <select
                aria-label="设备状态"
                value={status}
                onChange={(event) => {
                  setStatus(event.target.value as Reachability | "");
                  setPage(1);
                }}
              >
                <option value="">全部状态</option>
                <option value="reachable">可达</option>
                <option value="unreachable">不可达</option>
                <option value="unknown">未采集</option>
              </select>
            </label>
            <span className="toolbar-count">共 {devices.data?.total ?? 0} 台设备</span>
          </div>

          {selectionMode ? (
            <div className="selection-bar">
              <span>
                已选择 <strong>{selected.length}</strong> 台设备
              </span>
              <button
                className="button button-quiet button-xs"
                type="button"
                onClick={() =>
                  setSelected((current) =>
                    allCurrentSelected
                      ? current.filter((name) => !currentPageNames.includes(name))
                      : Array.from(new Set([...current, ...currentPageNames]))
                  )
                }
              >
                {allCurrentSelected ? "取消当前页全选" : "全选当前页"}
              </button>
              <button
                className="button button-danger button-xs"
                type="button"
                disabled={!selected.length}
                onClick={() => setDeleteOpen(true)}
              >
                退役选中设备
              </button>
            </div>
          ) : null}

          {devices.isLoading ? (
            <LoadingState label="正在读取设备台账" />
          ) : devices.isError ? (
            <EmptyState
              title="设备台账加载失败"
              description={devices.error instanceof Error ? devices.error.message : "请稍后重试"}
            />
          ) : deviceItems.length ? (
            <>
              <div className="table-scroll">
                <table className="data-table device-table">
                  <thead>
                    <tr>
                      {selectionMode ? <th className="selection-cell" aria-label="选择" /> : null}
                      <th>状态</th>
                      <th>设备</th>
                      <th>站点</th>
                      <th>管理地址</th>
                      <th>平台</th>
                      <th>最近采集</th>
                      <th>采集摘要</th>
                      <th aria-label="操作" />
                    </tr>
                  </thead>
                  <tbody>
                    {deviceItems.map((device) => (
                      <AssetRow
                        key={device.id}
                        device={device}
                        selecting={selectionMode}
                        selected={selected.includes(device.name)}
                        onSelect={(checked) =>
                          setSelected((current) =>
                            checked ? Array.from(new Set([...current, device.name])) : current.filter((name) => name !== device.name)
                          )
                        }
                        onOpen={(anchor) => openDeviceDrawer(device, anchor)}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
              <Pagination
                page={page}
                pageSize={pageSize}
                total={devices.data?.total ?? 0}
                onPageChange={setPage}
                onPageSizeChange={(size) => {
                  setPageSize(size);
                  setPage(1);
                }}
              />
            </>
          ) : (
            <EmptyState
              title="没有匹配的设备"
              description="调整搜索条件或添加一台设备。未采集设备会自动使用未知状态。"
              action={
                <button className="button button-primary" type="button" onClick={() => setAddOpen(true)}>
                  添加设备
                </button>
              }
            />
          )}
        </Panel>
      ) : (
        <Panel className="asset-table-panel">
          {runs.isLoading ? (
            <LoadingState label="正在读取采集批次" />
          ) : runs.data?.items.length ? (
            <>
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>批次</th>
                      <th>触发</th>
                      <th>范围</th>
                      <th>状态</th>
                      <th>设备结果</th>
                      <th>SSH 命令</th>
                      <th>连通性</th>
                      <th>发布</th>
                    </tr>
                  </thead>
                  <tbody>
                    {runs.data.items.map((run) => (
                      <tr key={run.id}>
                        <td>
                          <span className="cell-primary mono">{run.id}</span>
                          <span className="cell-secondary">{new Date(run.startedAt).toLocaleString("zh-CN")}</span>
                        </td>
                        <td><span className="tag plain">{run.trigger}</span></td>
                        <td>{run.scope}</td>
                        <td><StatusBadge tone={runTone(run.status)}>{run.status}</StatusBadge></td>
                        <td>{run.deviceSummary}</td>
                        <td>{run.sshSummary}</td>
                        <td>{run.connectivitySummary}</td>
                        <td>{run.publishedAt ? new Date(run.publishedAt).toLocaleTimeString("zh-CN") : "未发布"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pagination
                page={page}
                pageSize={pageSize}
                total={runs.data.total}
                onPageChange={setPage}
                onPageSizeChange={(size) => {
                  setPageSize(size);
                  setPage(1);
                }}
              />
            </>
          ) : (
            <EmptyState
              title="尚无采集批次"
              description="启动采集后会在这里显示设备结果、命令执行和发布状态。"
              icon={<Database size={22} />}
            />
          )}
        </Panel>
      )}

      <Dialog.Root open={addOpen} onOpenChange={setAddOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="dialog-content dialog-wide">
            <Dialog.Title className="dialog-title">添加设备</Dialog.Title>
            <Dialog.Description className="dialog-description">
              保存后会生成新的设备台账版本。新增设备在首次采集前保持未采集状态。
            </Dialog.Description>
            <div className="dialog-form-grid">
              <Field label="设备名称">
                <input
                  className="field"
                  value={form.name}
                  placeholder="例如 BR-C-R4"
                  onChange={(event) => setForm({ ...form, name: event.target.value })}
                />
              </Field>
              <Field label="角色">
                <select className="field" value={form.role} onChange={(event) => setForm({ ...form, role: event.target.value })}>
                  <option>路由器</option>
                  <option>交换机</option>
                  <option>测试终端</option>
                  <option>服务器</option>
                </select>
              </Field>
              <Field label="站点">
                <select className="field" value={form.site} onChange={(event) => setForm({ ...form, site: event.target.value })}>
                  <option>总部</option>
                  <option>分支A</option>
                  <option>分支B</option>
                </select>
              </Field>
              <Field label="设备类型">
                <select
                  className="field"
                  value={form.deviceType}
                  onChange={(event) => setForm({ ...form, deviceType: event.target.value })}
                >
                  <option value="cisco_ios">Cisco IOS</option>
                  <option value="cisco_ios">Cisco IOSvL2</option>
                  <option value="linux">Linux / VPCS</option>
                </select>
              </Field>
              <Field label="厂商">
                <input className="field" value={form.vendor} onChange={(event) => setForm({ ...form, vendor: event.target.value })} />
              </Field>
              <Field label="型号">
                <input className="field" value={form.model} onChange={(event) => setForm({ ...form, model: event.target.value })} />
              </Field>
              <Field label="管理地址">
                <input
                  className="field mono"
                  value={form.managementIp}
                  placeholder="192.168.249.20"
                  onChange={(event) => setForm({ ...form, managementIp: event.target.value })}
                />
              </Field>
              <Field label="SSH / SNMP 端口">
                <div className="inline-fields">
                  <input
                    className="field"
                    type="number"
                    value={form.sshPort}
                    onChange={(event) => setForm({ ...form, sshPort: Number(event.target.value) })}
                  />
                  <input
                    className="field"
                    type="number"
                    value={form.snmpPort}
                    onChange={(event) => setForm({ ...form, snmpPort: Number(event.target.value) })}
                  />
                </div>
              </Field>
              <Field label="接口列表" hint="多个接口使用逗号分隔">
                <input
                  className="field"
                  value={form.interfaces}
                  onChange={(event) => setForm({ ...form, interfaces: event.target.value })}
                />
              </Field>
            </div>
            {createDevice.isError ? (
              <div className="inline-error">
                {createDevice.error instanceof Error ? createDevice.error.message : "设备保存失败"}
              </div>
            ) : null}
            <div className="dialog-actions">
              <Dialog.Close asChild>
                <button className="button button-quiet" type="button">取消</button>
              </Dialog.Close>
              <button
                className="button button-primary"
                type="button"
                disabled={!form.name.trim() || createDevice.isPending}
                onClick={() =>
                  void createDevice.mutateAsync({
                    ...form,
                    name: form.name.trim(),
                    interfaces: form.interfaces
                      .split(",")
                      .map((item) => item.trim())
                      .filter(Boolean)
                  })
                }
              >
                {createDevice.isPending ? <LoaderCircle className="spin" size={15} /> : <Plus size={15} />}
                保存设备
              </button>
            </div>
            <Dialog.Close asChild>
              <button className="icon-button dialog-close" aria-label="关闭" type="button">
                <X size={16} />
              </button>
            </Dialog.Close>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title="退役选中设备"
        description={`将 ${selected.length} 台设备标记为退役。历史采集、诊断证据和审计引用会继续保留。`}
        confirmLabel="确认退役"
        tone="danger"
        onConfirm={async () => {
          await archiveDevices.mutateAsync(selected);
        }}
        confirmDisabled={archiveDevices.isPending || !selected.length}
      >
        <div className="selected-device-list">
          {selected.map((name) => (
            <span className="tag plain" key={name}>{name}</span>
          ))}
        </div>
      </ConfirmDialog>

      <Dialog.Root open={scheduleOpen} onOpenChange={setScheduleOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="dialog-content dialog-wide">
            <Dialog.Title className="dialog-title">采集设置</Dialog.Title>
            <Dialog.Description className="dialog-description">
              服务端保存采集频率、时段、范围、并发、超时和重试策略。修改不会自动覆盖正在运行的批次。
            </Dialog.Description>
            <div className="dialog-form-grid">
              <Field label="启用自动采集">
                <label className="switch-row">
                  <input
                    type="checkbox"
                    checked={scheduleForm.enabled}
                    onChange={(event) => setScheduleForm({ ...scheduleForm, enabled: event.target.checked })}
                  />
                  <span>{scheduleForm.enabled ? "按计划自动执行" : "暂停计划"}</span>
                </label>
              </Field>
              <Field label="采集频率（分钟）">
                <input
                  className="field"
                  type="number"
                  min={1}
                  value={scheduleForm.frequency_minutes}
                  onChange={(event) => setScheduleForm({ ...scheduleForm, frequency_minutes: Number(event.target.value) })}
                />
              </Field>
              <Field label="开始时间">
                <input
                  className="field"
                  type="time"
                  value={scheduleForm.active_window.start}
                  onChange={(event) => setScheduleForm({
                    ...scheduleForm,
                    active_window: { ...scheduleForm.active_window, start: event.target.value }
                  })}
                />
              </Field>
              <Field label="结束时间">
                <input
                  className="field"
                  type="time"
                  value={scheduleForm.active_window.end}
                  onChange={(event) => setScheduleForm({
                    ...scheduleForm,
                    active_window: { ...scheduleForm.active_window, end: event.target.value }
                  })}
                />
              </Field>
              <Field label="采集范围">
                <select
                  className="field"
                  value={scheduleForm.scope}
                  onChange={(event) => setScheduleForm({ ...scheduleForm, scope: event.target.value })}
                >
                  <option value="all">全部活动设备</option>
                  <option value="problem">仅异常设备和未采集设备</option>
                </select>
              </Field>
              <Field label="并发设备数">
                <input
                  className="field"
                  type="number"
                  min={1}
                  max={32}
                  value={scheduleForm.concurrency}
                  onChange={(event) => setScheduleForm({ ...scheduleForm, concurrency: Number(event.target.value) })}
                />
              </Field>
              <Field label="单设备超时（秒）">
                <input
                  className="field"
                  type="number"
                  min={1}
                  value={scheduleForm.timeout_seconds}
                  onChange={(event) => setScheduleForm({ ...scheduleForm, timeout_seconds: Number(event.target.value) })}
                />
              </Field>
              <Field label="失败重试次数">
                <input
                  className="field"
                  type="number"
                  min={0}
                  max={5}
                  value={scheduleForm.retries}
                  onChange={(event) => setScheduleForm({ ...scheduleForm, retries: Number(event.target.value) })}
                />
              </Field>
            </div>
            <div className="dialog-actions">
              <Dialog.Close asChild>
                <button className="button button-quiet" type="button">取消</button>
              </Dialog.Close>
              <button
                className="button button-primary"
                type="button"
                disabled={saveSchedule.isPending}
                onClick={() => saveSchedule.mutate(scheduleForm)}
              >
                {saveSchedule.isPending ? <LoaderCircle className="spin" size={15} /> : null}
                保存采集计划
              </button>
            </div>
            <Dialog.Close asChild>
              <button className="icon-button dialog-close" aria-label="关闭" type="button">
                <X size={16} />
              </button>
            </Dialog.Close>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}
