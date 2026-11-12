// The browser's only door: everything goes to the api-gateway at /api.

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

export const devUser = {
  get(): string | null {
    try { return localStorage.getItem("sdp.devUser"); } catch { return null; }
  },
  set(id: string | null) {
    try { id ? localStorage.setItem("sdp.devUser", id) : localStorage.removeItem("sdp.devUser"); } catch { /* private mode */ }
  },
};

type Opts = { method?: string; body?: unknown; form?: FormData };

export async function api<T = any>(path: string, opts: Opts = {}): Promise<T> {
  const headers: Record<string, string> = {};
  const u = devUser.get();
  if (u) headers["X-Dev-User"] = u;
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  const r = await fetch(`/api${path}`, { method: opts.method ?? (body ? "POST" : "GET"), headers, body });
  const type = r.headers.get("content-type") ?? "";
  if (!r.ok) {
    let code = "error", message = `Request failed (${r.status}).`;
    try {
      const j = await r.json();
      const d = j.detail;
      if (typeof d === "string") message = d;
      else if (d?.message) { message = d.message; code = d.code ?? code; }
      else if (Array.isArray(d)) message = d.map((e: any) => e.msg).join("; ");
    } catch { /* not json */ }
    throw new ApiError(r.status, code, message);
  }
  if (r.status === 204) return undefined as T;
  if (type.includes("json")) return r.json();
  return (await r.text()) as T;
}
