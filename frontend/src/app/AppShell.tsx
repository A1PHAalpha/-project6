import {
  Activity,
  BookOpen,
  ChevronLeft,
  ChevronRight,
  ClipboardList,
  Database,
  Gauge,
  LogOut,
  Menu,
  Search,
  Settings,
  ShieldCheck,
  Sparkles,
  X
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api } from "../shared/api/client";
import { DeviceDetailDrawer } from "../shared/components/DeviceDetailDrawer";
import { TerminalDock } from "../shared/components/TerminalDock";
import { useWorkspace } from "../shared/state/workspace";

const navItems = [
  { to: "/overview", label: "总览", icon: Gauge },
  { to: "/assets", label: "设备与采集", icon: Database },
  { to: "/diagnosis", label: "诊断中心", icon: Sparkles },
  { to: "/knowledge", label: "运维知识库", icon: BookOpen }
];

const pageNames: Record<string, string> = {
  "/overview": "总览",
  "/assets": "设备与采集",
  "/diagnosis": "诊断中心",
  "/knowledge": "运维知识库",
  "/settings": "设置"
};

export function AppShell() {
  const location = useLocation();
  const navigate = useNavigate();
  const collapsed = useWorkspace((state) => state.sidebarCollapsed);
  const toggleSidebar = useWorkspace((state) => state.toggleSidebar);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [collecting, setCollecting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const overview = useQuery({ queryKey: ["overview"], queryFn: api.getOverview });
  const latestRun = overview.data?.latestRun;

  async function triggerCollection() {
    setCollecting(true);
    setNotice(null);
    try {
      await api.triggerCollection();
      setNotice("已创建采集任务，可在设备与采集页查看进度。");
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "采集任务创建失败");
    } finally {
      setCollecting(false);
    }
  }

  return (
    <div className={`app-shell ${collapsed ? "sidebar-collapsed" : ""}`}>
      <aside className={`sidebar ${mobileNavOpen ? "mobile-open" : ""}`}>
        <div className="brand">
          <div className="brand-mark">N</div>
          <div className="brand-copy">
            <strong>NetOps Console</strong>
            <span>企业网络智能运维台</span>
          </div>
          <button
            className="icon-button mobile-nav-close"
            type="button"
            aria-label="关闭导航"
            onClick={() => setMobileNavOpen(false)}
          >
            <X size={17} />
          </button>
        </div>

        <nav className="sidebar-nav" aria-label="主导航">
          <span className="nav-group-label">运维工作台</span>
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <NavLink
                key={item.to}
                to={item.to}
                title={collapsed ? item.label : undefined}
                onClick={() => setMobileNavOpen(false)}
                className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}
              >
                <Icon size={17} />
                <span>{item.label}</span>
                {item.to === "/diagnosis" ? <em className="nav-count">1</em> : null}
              </NavLink>
            );
          })}
          <span className="nav-group-label">系统</span>
          <NavLink
            to="/settings"
            title={collapsed ? "设置" : undefined}
            onClick={() => setMobileNavOpen(false)}
            className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}
          >
            <Settings size={17} />
            <span>设置</span>
          </NavLink>
        </nav>

        <footer className="sidebar-footer">
          <div className="system-health">
            <span className="status-dot success" />
            <div>
              <strong>采集服务运行中</strong>
              <span>EVE-NG 实验网 · 核心链路</span>
            </div>
          </div>
          <div className="sidebar-security">
            <ShieldCheck size={14} />
            操作审计已启用
          </div>
        </footer>

        <button className="sidebar-toggle" type="button" onClick={toggleSidebar} aria-label="切换侧栏">
          {collapsed ? <ChevronRight size={15} /> : <ChevronLeft size={15} />}
        </button>
      </aside>

      <div className="workspace">
        <header className="topbar">
          <div className="topbar-context">
            <button
              className="icon-button mobile-menu"
              type="button"
              aria-label="打开导航"
              onClick={() => setMobileNavOpen(true)}
            >
              <Menu size={18} />
            </button>
            <span>实验网</span>
            <span className="crumb-separator">/</span>
            <strong>{pageNames[location.pathname] ?? "NetOps Console"}</strong>
            {api.mockMode ? <span className="demo-chip">MOCK · 示例数据</span> : <span className="live-chip">实时数据</span>}
          </div>
          <div className="topbar-actions">
            <label className="global-search">
              <Search size={15} />
              <input
                value={search}
                placeholder="搜索设备、诊断、知识…"
                onChange={(event) => setSearch(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && search.trim()) {
                    navigate(`/assets?q=${encodeURIComponent(search.trim())}`);
                  }
                }}
              />
            </label>
            <div className="last-collection">
              <Activity size={14} />
              <span>
                最近采集
                <strong>
                  {latestRun
                    ? new Date(latestRun.publishedAt ?? latestRun.startedAt).toLocaleString("zh-CN", {
                        month: "2-digit",
                        day: "2-digit",
                        hour: "2-digit",
                        minute: "2-digit"
                      })
                    : "尚无采集"}
                </strong>
              </span>
            </div>
            <button
              className="button button-quiet button-sm"
              type="button"
              disabled={collecting}
              onClick={() => void triggerCollection()}
            >
              {collecting ? "创建中…" : "立即采集"}
            </button>
            <div className="user-chip">
              <ClipboardList size={15} />
              <span>
                运维管理员
                <small>单用户 · 实验环境</small>
              </span>
            </div>
            <a className="icon-button" href="/logout" aria-label="退出登录" title="退出登录">
              <LogOut size={16} />
            </a>
          </div>
        </header>

        {notice ? (
          <div className="top-notice" role="status">
            <span>{notice}</span>
            <button className="icon-button" type="button" onClick={() => setNotice(null)} aria-label="关闭提示">
              <X size={14} />
            </button>
          </div>
        ) : null}

        <main className="page-content">
          <Outlet />
        </main>
      </div>

      <DeviceDetailDrawer />
      <TerminalDock />
    </div>
  );
}
