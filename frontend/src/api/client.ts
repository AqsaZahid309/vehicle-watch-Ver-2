/**
 * Thin fetch wrapper: attaches the bearer token, refreshes it once on 401,
 * and turns API errors into `ApiError` with the server's `detail` message.
 */

const ACCESS = "vw-access";
const REFRESH = "vw-refresh";
export const API = "/api/v1";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export const tokens = {
  get access() {
    return read(ACCESS);
  },
  get refresh() {
    return read(REFRESH);
  },
  set(access: string, refresh: string) {
    try {
      localStorage.setItem(ACCESS, access);
      localStorage.setItem(REFRESH, refresh);
    } catch {
      /* private mode — session only */
    }
  },
  clear() {
    try {
      localStorage.removeItem(ACCESS);
      localStorage.removeItem(REFRESH);
    } catch {
      /* ignore */
    }
  },
};

let onUnauthorized: () => void = () => {};
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

let refreshing: Promise<boolean> | null = null;
async function tryRefresh(): Promise<boolean> {
  const rt = tokens.refresh;
  if (!rt) return false;
  refreshing ??= (async () => {
    try {
      const r = await fetch(`${API}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: rt }),
      });
      if (!r.ok) return false;
      const t = await r.json();
      tokens.set(t.access_token, t.refresh_token);
      return true;
    } catch {
      return false;
    } finally {
      setTimeout(() => (refreshing = null), 0);
    }
  })();
  return refreshing;
}

async function errorMessage(r: Response): Promise<string> {
  try {
    const body = await r.json();
    if (typeof body.detail === "string") {
      if (Array.isArray(body.errors) && body.errors.length) {
        const e = body.errors[0];
        const field = Array.isArray(e.loc) ? e.loc.filter((x: unknown) => x !== "body").join(".") : "";
        return `${field ? field + ": " : ""}${e.msg}`;
      }
      return body.detail;
    }
    return JSON.stringify(body.detail ?? body);
  } catch {
    return `${r.status} ${r.statusText}`;
  }
}

export async function request<T>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const access = tokens.access;
  if (access) headers.set("Authorization", `Bearer ${access}`);

  const r = await fetch(path.startsWith("/") ? path : `${API}/${path}`, { ...init, headers });
  if (r.status === 401 && retry && tokens.refresh) {
    if (await tryRefresh()) return request<T>(path, init, false);
  }
  if (r.status === 401) {
    onUnauthorized();
  }
  if (!r.ok) throw new ApiError(r.status, await errorMessage(r));
  if (r.status === 204) return undefined as T;
  const ct = r.headers.get("content-type") ?? "";
  return (ct.includes("application/json") ? r.json() : r.text()) as Promise<T>;
}

function qs(params?: Record<string, unknown>): string {
  if (!params) return "";
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") s.set(k, String(v));
  }
  const str = s.toString();
  return str ? `?${str}` : "";
}

export const api = {
  get: <T>(path: string, params?: Record<string, unknown>) => request<T>(`${API}${path}${qs(params)}`),
  post: <T>(path: string, body?: unknown) =>
    request<T>(`${API}${path}`, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
  patch: <T>(path: string, body: unknown) => request<T>(`${API}${path}`, { method: "PATCH", body: JSON.stringify(body) }),
  del: <T = void>(path: string) => request<T>(`${API}${path}`, { method: "DELETE" }),
  /** Download a file (CSV export) with auth. */
  async download(path: string, params: Record<string, unknown>, filename: string) {
    const headers: HeadersInit = tokens.access ? { Authorization: `Bearer ${tokens.access}` } : {};
    const r = await fetch(`${API}${path}${qs(params)}`, { headers });
    if (!r.ok) throw new ApiError(r.status, await errorMessage(r));
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  },
};
