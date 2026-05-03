const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000/api/v1";

export type Role =
  | "admin"
  | "cfo"
  | "project_director"
  | "epc_manager"
  | "site_manager"
  | "civil_engineer"
  | "subcontractor"
  | "supplier"
  | "viewer";

function token(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem("epc_token");
}

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const t = token();
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(t ? { Authorization: `Bearer ${t}` } : {}),
      ...(init.headers ?? {}),
    },
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

export const api = {
  login: (email: string, password: string) =>
    req<{ access_token: string; role: Role }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  // CFO
  projectSummary: (id: number) =>
    req<{
      project_id: number;
      code: string;
      revenue: number | null;
      actual_cost: number | null;
      margin: number | null;
      margin_percent: number | null;
      field_idle_cost: number | null;
    }>(`/cfo/projects/${id}/summary`),

  pendingApprovals: () => req<unknown[]>("/cfo/gatekeeper/approvals"),

  updateVisibility: (rows: { role: Role; allowed_fields: string[] }[]) =>
    req("/cfo/visibility-policy", { method: "PUT", body: JSON.stringify(rows) }),

  // ERP
  syncErp: (vendor: "oracle" | "sap", projectCode: string) =>
    req<{ vendor: string; commitments_pulled: number }>(
      `/erp/${vendor}/sync?project_code=${encodeURIComponent(projectCode)}`,
      { method: "POST" },
    ),

  // Scheduler
  gantt: (id: number) => req<unknown[]>(`/scheduler/projects/${id}/gantt`),
  recomputeCpm: (id: number) =>
    req(`/scheduler/projects/${id}/cpm`, { method: "POST" }),
  submitDailyLog: (body: {
    project_id: number;
    activity_id: string;
    raw_transcript: string;
    crew_count?: number;
  }) => req("/scheduler/daily-log", { method: "POST", body: JSON.stringify(body) }),

  // Risk
  wrapScore: (id: number) =>
    req<{
      score: number;
      schedule_factor: number;
      rfc_factor: number;
      permit_factor: number;
      long_lead_factor: number;
      field_idle_factor: number;
    }>(`/risk/projects/${id}/wrap-score`),

  rfcMisses: (id: number) =>
    req<unknown[]>(`/risk/projects/${id}/rfc-misses`),
};
