// Reusable presentation primitives shared by every console feature.
import * as Dialog from "@radix-ui/react-dialog";
import {
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  DatabaseBackup,
  LoaderCircle,
  X
} from "lucide-react";
import type { ReactNode } from "react";
import type { Tone } from "../types";

export function StatusBadge({
  tone,
  children,
  dot = true
}: {
  tone: Tone;
  children: ReactNode;
  dot?: boolean;
}) {
  return (
    <span className={`status-badge tone-${tone}`}>
      {dot ? <span className="status-dot" aria-hidden="true" /> : null}
      {children}
    </span>
  );
}

export function KpiCard({
  label,
  value,
  meta,
  tone = "accent",
  suffix
}: {
  label: string;
  value: ReactNode;
  meta: ReactNode;
  tone?: Tone;
  suffix?: ReactNode;
}) {
  return (
    <article className="kpi-card">
      <div className={`card-eyebrow tone-${tone}`}>
        <span className="status-dot" aria-hidden="true" />
        {label}
      </div>
      <div className={`kpi-value text-${tone}`}>
        {value}
        {suffix ? <small>{suffix}</small> : null}
      </div>
      <div className="kpi-meta">{meta}</div>
    </article>
  );
}

export function Panel({
  title,
  subtitle,
  action,
  className = "",
  children
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={`panel ${className}`}>
      {title || action ? (
        <header className="panel-head">
          <div>
            <h2 className="panel-title">
              <span className="status-dot accent" aria-hidden="true" />
              {title}
            </h2>
            {subtitle ? <p className="panel-subtitle">{subtitle}</p> : null}
          </div>
          {action ? <div className="panel-actions">{action}</div> : null}
        </header>
      ) : null}
      {children}
    </section>
  );
}

export function PageHeader({
  title,
  description,
  actions,
  meta
}: {
  title: string;
  description: string;
  actions?: ReactNode;
  meta?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        <p>{description}</p>
        {meta ? <div className="page-meta">{meta}</div> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  );
}

export function SettingsGroup({
  title,
  description,
  action,
  children
}: {
  title: string;
  description: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="panel settings-group">
      <header className="panel-head">
        <div>
          <h2 className="panel-title">{title}</h2>
          <p className="panel-subtitle">{description}</p>
        </div>
        {action ? <div className="panel-actions">{action}</div> : null}
      </header>
      <div className="settings-grid">{children}</div>
    </section>
  );
}

export function Field({
  label,
  hint,
  children
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="field-group">
      <span className="field-label">{label}</span>
      {children}
      {hint ? <span className="field-hint">{hint}</span> : null}
    </label>
  );
}

export function EmptyState({
  title,
  description,
  action,
  icon
}: {
  title: string;
  description: string;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <div className="empty-icon">{icon ?? <CircleHelp size={22} />}</div>
      <strong>{title}</strong>
      <p>{description}</p>
      {action}
    </div>
  );
}

export function LoadingState({ label = "正在加载数据" }: { label?: string }) {
  return (
    <div className="loading-state">
      <LoaderCircle className="spin" size={20} />
      <span>{label}</span>
    </div>
  );
}

export function SegmentedControl<T extends string>({
  value,
  options,
  onChange
}: {
  value: T;
  options: Array<{ value: T; label: ReactNode }>;
  onChange: (value: T) => void;
}) {
  return (
    <div className="segmented-control" role="tablist">
      {options.map((option) => (
        <button
          type="button"
          role="tab"
          aria-selected={option.value === value}
          className={option.value === value ? "active" : ""}
          key={option.value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function Pagination({
  page,
  pageSize,
  total,
  onPageChange,
  onPageSizeChange
}: {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  onPageSizeChange?: (size: number) => void;
}) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  return (
    <footer className="pagination">
      <span>
        共 {total} 项 · 第 {page} / {pageCount} 页
      </span>
      <div className="pagination-actions">
        <button
          type="button"
          className="button button-quiet button-sm"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
        >
          <ChevronLeft size={14} />
          上一页
        </button>
        <button
          type="button"
          className="button button-quiet button-sm"
          disabled={page >= pageCount}
          onClick={() => onPageChange(page + 1)}
        >
          下一页
          <ChevronRight size={14} />
        </button>
        {onPageSizeChange ? (
          <select
            className="field field-compact"
            aria-label="每页数量"
            value={pageSize}
            onChange={(event) => onPageSizeChange(Number(event.target.value))}
          >
            {[5, 8, 10, 20].map((size) => (
              <option key={size} value={size}>
                每页 {size} 项
              </option>
            ))}
          </select>
        ) : null}
      </div>
    </footer>
  );
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  tone = "accent",
  children,
  onConfirm,
  confirmDisabled
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  confirmLabel: string;
  tone?: "accent" | "danger";
  children?: ReactNode;
  onConfirm: () => void | Promise<void>;
  confirmDisabled?: boolean;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog-content">
          <Dialog.Title className="dialog-title">{title}</Dialog.Title>
          <Dialog.Description className="dialog-description">{description}</Dialog.Description>
          {children ? <div className="dialog-body">{children}</div> : null}
          <div className="dialog-actions">
            <Dialog.Close asChild>
              <button className="button button-quiet" type="button">
                取消
              </button>
            </Dialog.Close>
            <button
              className={`button ${tone === "danger" ? "button-danger" : "button-primary"}`}
              disabled={confirmDisabled}
              type="button"
              onClick={() => void onConfirm()}
            >
              {confirmLabel}
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
  );
}

export function BackupReminder({
  destructive,
  deviceName
}: {
  destructive?: boolean;
  deviceName: string;
}) {
  return (
    <div className={`backup-reminder ${destructive ? "destructive" : ""}`}>
      <span className="backup-reminder-icon">
        {destructive ? <AlertTriangle size={19} /> : <DatabaseBackup size={19} />}
      </span>
      <div>
        <strong>{destructive ? "写操作风险提示" : "操作前请确认配置备份"}</strong>
        <p>
          {destructive
            ? `${deviceName} 即将执行可能修改配置的命令。请先完成当前运行配置备份，并确认影响范围与回滚步骤。`
            : `调起 ${deviceName} 终端前，请先完成当前运行配置备份；终端内的所有命令与输出都会进入操作记录。`}
        </p>
      </div>
    </div>
  );
}
