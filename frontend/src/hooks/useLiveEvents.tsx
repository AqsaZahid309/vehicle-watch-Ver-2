import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Reading } from "../api/types";
import { useToast } from "../components/Toasts";

/**
 * One Server-Sent Events connection per signed-in tab. Telemetry updates the
 * live-positions cache in place; alerts, notifications and work-order changes
 * invalidate the affected queries so every page stays current without polling.
 */

type Listener = (type: string, data: any) => void;

interface LiveState {
  connected: boolean;
  subscribe: (fn: Listener) => () => void;
}

const Ctx = createContext<LiveState>({ connected: false, subscribe: () => () => {} });

export function LiveEventsProvider({ enabled, children }: { enabled: boolean; children: ReactNode }) {
  const [connected, setConnected] = useState(false);
  const listeners = useRef(new Set<Listener>());
  const qc = useQueryClient();
  const toast = useToast();

  useEffect(() => {
    if (!enabled) return;
    let es: EventSource | null = null;
    let stopped = false;
    let retry: ReturnType<typeof setTimeout>;
    let lastInvalidate = 0;

    const connect = async () => {
      try {
        const { ticket } = await api.post<{ ticket: string }>("/stream/ticket");
        if (stopped) return;
        es = new EventSource(`/api/v1/stream?ticket=${encodeURIComponent(ticket)}`);
        es.addEventListener("ready", () => setConnected(true));
        es.onmessage = (e) => {
          let msg: { type: string; data: any };
          try {
            msg = JSON.parse(e.data);
          } catch {
            return;
          }
          handle(msg.type, msg.data);
          listeners.current.forEach((fn) => fn(msg.type, msg.data));
        };
        es.onerror = () => {
          setConnected(false);
          es?.close();
          if (!stopped) retry = setTimeout(connect, 5000);
        };
      } catch {
        setConnected(false);
        if (!stopped) retry = setTimeout(connect, 10000);
      }
    };

    const handle = (type: string, data: any) => {
      if (type === "telemetry") {
        const r = data as Reading & { device_id: string };
        qc.setQueryData(["live"], (old: any[] | undefined) =>
          old?.map((d) => (d.id === r.device_id ? { ...d, latest: r, online: true, last_seen_at: r.recorded_at } : d)),
        );
        const now = Date.now();
        if (now - lastInvalidate > 15000) {
          lastInvalidate = now;
          qc.invalidateQueries({ queryKey: ["fleet-summary"] });
        }
      } else if (type === "alert" || type === "alert_updated") {
        qc.invalidateQueries({ queryKey: ["alerts"] });
        qc.invalidateQueries({ queryKey: ["fleet-summary"] });
        qc.invalidateQueries({ queryKey: ["live"] });
      } else if (type === "notification") {
        qc.invalidateQueries({ queryKey: ["notifications"] });
        if (data.severity === "CRITICAL" || data.severity === "MEDIUM") {
          toast({ title: data.title, body: data.body?.slice(0, 180), kind: data.severity });
        }
      } else if (type === "work_order") {
        qc.invalidateQueries({ queryKey: ["work-orders"] });
      }
    };

    void connect();
    return () => {
      stopped = true;
      clearTimeout(retry);
      es?.close();
      setConnected(false);
    };
  }, [enabled, qc, toast]);

  const subscribe = (fn: Listener) => {
    listeners.current.add(fn);
    return () => {
      listeners.current.delete(fn);
    };
  };

  return <Ctx.Provider value={{ connected, subscribe }}>{children}</Ctx.Provider>;
}

export const useLiveEvents = () => useContext(Ctx);
