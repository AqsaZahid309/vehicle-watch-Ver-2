import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, setUnauthorizedHandler, tokens } from "../api/client";
import type { Me } from "../api/types";

interface AuthState {
  user: Me | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (data: { email: string; password: string; full_name?: string; organization_name?: string }) => Promise<void>;
  logout: () => void;
  reload: () => Promise<void>;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const qc = useQueryClient();

  const logout = useCallback(() => {
    tokens.clear();
    setUser(null);
    qc.clear();
  }, [qc]);

  const reload = useCallback(async () => {
    if (!tokens.access) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      setUser(await api.get<Me>("/auth/me"));
    } catch {
      tokens.clear();
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      tokens.clear();
      setUser(null);
    });
    void reload();
  }, [reload]);

  const login = useCallback(
    async (email: string, password: string) => {
      const t = await api.post<{ access_token: string; refresh_token: string }>("/auth/login", { email, password });
      tokens.set(t.access_token, t.refresh_token);
      await reload();
    },
    [reload],
  );

  const register = useCallback(
    async (data: { email: string; password: string; full_name?: string; organization_name?: string }) => {
      await api.post("/auth/register", data);
      await login(data.email, data.password);
    },
    [login],
  );

  const value = useMemo(() => ({ user, loading, login, register, logout, reload }), [user, loading, login, register, logout, reload]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
