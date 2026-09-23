import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bell, BrainCircuit, Car, FileBarChart, Fuel, Gauge, LogOut, Map, MapPinned, Menu, Moon, Route,
  Settings, ShieldAlert, Sun, Truck, Wrench,
} from "lucide-react";
import { api } from "../api/client";
import type { Notification } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { useLiveEvents } from "../hooks/useLiveEvents";
import { timeAgo } from "../lib/format";
import { SeverityBadge } from "./ui";

function useTheme(): [string, () => void] {
  const [theme, setTheme] = useState(() => {
    const saved = document.documentElement.dataset.theme;
    if (saved) return saved;
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("vw-theme", theme);
    } catch {
      /* ignore */
    }
  }, [theme]);
  return [theme, () => setTheme((t) => (t === "dark" ? "light" : "dark"))];
}

function NotificationBell() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const nav = useNavigate();
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["notifications"],
    queryFn: () => api.get<{ items: Notification[]; unread: number }>("/notifications", { limit: 30 }),
    refetchInterval: 60_000,
  });
  const readAll = useMutation({
    mutationFn: () => api.post("/notifications/read-all"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notifications"] }),
  });

  useEffect(() => {
    const h = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);

  const unread = data?.unread ?? 0;
  return (
    <div className="relative" ref={ref}>
      <button className="btn ghost icon" aria-label={`Notifications (${unread} unread)`} onClick={() => setOpen((o) => !o)}>
        <Bell size={18} />
        {unread > 0 && (
          <span className="badge red" style={{ position: "absolute", top: -2, right: -4, padding: "0 5px", fontSize: 10 }}>
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>
      {open && (
        <div className="popover">
          <div className="card-head">
            <h3>Notifications</h3>
            <button className="btn sm ghost" disabled={!unread} onClick={() => readAll.mutate()}>Mark all read</button>
          </div>
          {!data?.items.length && <div className="empty small">You're all caught up.</div>}
          {data?.items.map((n) => (
            <div
              key={n.id}
              className="clickable"
              style={{ padding: "10px 16px", borderBottom: "1px solid var(--border)", cursor: "pointer", opacity: n.read ? 0.65 : 1 }}
              onClick={() => {
                setOpen(false);
                void api.post(`/notifications/${n.id}/read`).then(() => qc.invalidateQueries({ queryKey: ["notifications"] }));
                if (n.link) nav(n.link);
              }}
            >
              <div className="row between">
                <strong className="ellipsis" style={{ fontSize: 13 }}>{n.title}</strong>
                <SeverityBadge value={n.severity} />
              </div>
              <div className="small muted" style={{ display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{n.body}</div>
              <div className="small muted">{timeAgo(n.created_at)} · {n.event_type.replace("_", " ").toLowerCase()}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const NAV = [
  { section: "Monitor" },
  { to: "/", label: "Overview", icon: Gauge, end: true },
  { to: "/map", label: "Live map", icon: Map },
  { to: "/vehicles", label: "Vehicles", icon: Truck },
  { to: "/alerts", label: "Alerts", icon: ShieldAlert, count: "alerts" },
  { section: "Operate" },
  { to: "/maintenance", label: "Maintenance", icon: Wrench },
  { to: "/trips", label: "Trips & drivers", icon: Route },
  { to: "/geofences", label: "Geofences", icon: MapPinned },
  { to: "/fuel", label: "Fuel", icon: Fuel },
  { section: "Insight" },
  { to: "/reports", label: "Reports", icon: FileBarChart },
  { to: "/models", label: "ML models", icon: BrainCircuit },
  { to: "/settings", label: "Settings", icon: Settings },
] as const;

export default function Layout() {
  const { user, logout } = useAuth();
  const { connected } = useLiveEvents();
  const [theme, toggleTheme] = useTheme();
  const [navOpen, setNavOpen] = useState(false);
  const loc = useLocation();
  useEffect(() => setNavOpen(false), [loc.pathname]);

  const { data: summary } = useQuery({
    queryKey: ["fleet-summary"],
    queryFn: () => api.get<{ unacknowledged_alerts: number }>("/analytics/fleet"),
    refetchInterval: 60_000,
  });

  return (
    <div className="app">
      <aside className={`sidebar ${navOpen ? "open" : ""}`}>
        <div className="brand">
          <span className="brand-mark"><Car size={18} /></span> VehicleWatch
        </div>
        <nav className="nav" aria-label="Main">
          {NAV.map((item, i) =>
            "section" in item ? (
              <div key={i} className="nav-section">{item.section}</div>
            ) : (
              <NavLink key={item.to} to={item.to} end={"end" in item} className={({ isActive }) => (isActive ? "active" : "")}>
                <item.icon size={17} /> {item.label}
                {"count" in item && !!summary?.unacknowledged_alerts && (
                  <span className="badge red count">{summary.unacknowledged_alerts}</span>
                )}
              </NavLink>
            ),
          )}
        </nav>
        <div className="sidebar-foot">
          <div className="ellipsis" style={{ fontWeight: 650, color: "var(--text)" }}>{user?.organization.name}</div>
          <div className="ellipsis">{user?.email}</div>
          <div><span className="badge blue" style={{ marginTop: 4 }}>{user?.role}</span></div>
        </div>
      </aside>
      {navOpen && <div className="overlay" style={{ zIndex: 25 }} onClick={() => setNavOpen(false)} />}

      <div className="main">
        <header className="topbar">
          <button className="btn ghost icon menu-btn" aria-label="Open menu" onClick={() => setNavOpen(true)}><Menu size={18} /></button>
          <span className="row small muted" title={connected ? "Receiving live events" : "Live stream disconnected — retrying"}>
            <span className={`dot ${connected ? "green pulse" : "red"}`} /> {connected ? "Live" : "Offline"}
          </span>
          <div className="spacer" />
          <button className="btn ghost icon" aria-label="Toggle theme" onClick={toggleTheme}>
            {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
          </button>
          <NotificationBell />
          <button className="btn ghost sm" onClick={logout}><LogOut size={16} /> Sign out</button>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
