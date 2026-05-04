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

  seedDemo: (id: number) =>
    req<{
      project_id: number;
      rfc_drawings: { id: number; drawing_no: string; rfc_due: string }[];
      permits: { id: number; permit_type: string; authority: string; target_date: string; status: string }[];
    }>(`/risk/projects/${id}/seed-demo`, { method: "POST" }),

  simulateRfcMiss: (
    id: number,
    body: {
      rfc_drawing_id: number;
      days_overdue: number;
      idle_crew: number;
      crew_burdened_rate: number;
    },
  ) =>
    req<{
      before_score: number;
      after_score: number;
      delta: number;
      idle_cost: number | null;
      factors_before: FactorVector;
      factors_after: FactorVector;
      claim_id: number | null;
      approval_id: number | null;
    }>(`/risk/projects/${id}/simulate-rfc-miss`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  // Visibility policy
  readVisibility: () =>
    req<{
      fields: { key: string; label: string }[];
      roles: Role[];
      policy: Record<Role, string[]>;
      viewer_role: Role;
    }>("/cfo/visibility-policy"),

  writeVisibility: (rows: { role: Role; allowed_fields: string[] }[]) =>
    req("/cfo/visibility-policy", { method: "PUT", body: JSON.stringify(rows) }),

  // Messaging
  sendMessage: (body: {
    project_id: number;
    thread_id?: number;
    subject?: string;
    body: string;
    context: {
      type: "activity" | "rfc" | "permit" | "none";
      activity_id?: string;
      rfc_drawing_id?: number;
      permit_id?: number;
    };
  }) => req("/messages/", { method: "POST", body: JSON.stringify(body) }),

  listThreads: (projectId: number) =>
    req<Array<{
      id: number;
      subject: string;
      created_at: string;
      messages: Array<{
        id: number;
        sender_email: string;
        body: string;
        activity_id: string | null;
        rfc_drawing_id: number | null;
        permit_id: number | null;
        created_at: string;
      }>;
    }>>(`/messages/threads?project_id=${projectId}`),

  // Claims
  listClaims: (projectId: number) =>
    req<ClaimSummary[]>(`/claims?project_id=${projectId}`),

  getClaim: (id: number) => req<ClaimSummary>(`/claims/${id}`),

  packetUrl: (id: number, format: "md" | "html" = "html") =>
    `${BASE}/claims/${id}/packet?format=${format}`,

  fetchPacket: async (id: number, format: "md" | "html" = "md"): Promise<string> => {
    const t = token();
    const res = await fetch(`${BASE}/claims/${id}/packet?format=${format}`, {
      headers: t ? { Authorization: `Bearer ${t}` } : {},
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.text();
  },

  approveApproval: (approvalId: number, decision: "approve" | "reject", notes?: string) =>
    req(`/cfo/gatekeeper/approvals/${approvalId}`, {
      method: "POST",
      body: JSON.stringify({ decision, notes }),
    }),

  finalizeClaim: (id: number) =>
    req<ClaimSummary>(`/claims/${id}/finalize`, { method: "POST" }),

  // Permit-delay simulation
  simulatePermitDelay: (
    id: number,
    body: {
      permit_id: number;
      days_overdue: number;
      idle_crew: number;
      crew_burdened_rate: number;
    },
  ) =>
    req<{
      before_score: number;
      after_score: number;
      delta: number;
      idle_cost: number | null;
      factors_before: FactorVector;
      factors_after: FactorVector;
      claim_id: number | null;
      approval_id: number | null;
    }>(`/risk/projects/${id}/simulate-permit-delay`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  // Notifications
  notifications: (projectId: number) =>
    req<NotificationFeedItem[]>(`/notifications?project_id=${projectId}`),

  evaluateNotifications: (projectId: number) =>
    req<NotificationFeedItem | null>("/notifications/evaluate", {
      method: "POST",
      body: JSON.stringify({ project_id: projectId, trigger: "manual" }),
    }),

  listRecipients: () => req<Recipient[]>("/notifications/recipients"),

  replaceRecipients: (rows: RecipientInput[]) =>
    req<Recipient[]>("/notifications/recipients", {
      method: "PUT",
      body: JSON.stringify(rows),
    }),

  addRecipient: (row: RecipientInput) =>
    req<Recipient>("/notifications/recipients", {
      method: "POST",
      body: JSON.stringify(row),
    }),

  deactivateRecipient: (id: number) =>
    req(`/notifications/recipients/${id}`, { method: "DELETE" }),

  // Comments
  listComments: (kind: "idle_event" | "claim", id: number) =>
    req<Comment[]>(`/comments?target_kind=${kind}&target_id=${id}`),

  postComment: (kind: "idle_event" | "claim", id: number, body: string) =>
    req<Comment>("/comments", {
      method: "POST",
      body: JSON.stringify({ target_kind: kind, target_id: id, body }),
    }),
};

export type NotificationFeedItem = {
  id: number;
  project_id: number;
  tier: "tier_1" | "tier_2" | "tier_3";
  trigger: string;
  subject: string;
  body: string;
  cpm_drift_days: number;
  open_idle_cost: number;
  idle_event_id: number | null;
  claim_id: number | null;
  dispatched_to: Array<{ channel: string; to: string; ok: boolean; tier: string }>;
  created_at: string;
};

export type RecipientInput = {
  name: string;
  role_label: string;
  email: string | null;
  phone: string | null;
  tiers: Array<"tier_1" | "tier_2" | "tier_3">;
  active: boolean;
};

export type Recipient = RecipientInput & {
  id: number;
  updated_by: string;
  updated_at: string;
};

export type Comment = {
  id: number;
  target_kind: string;
  target_id: number;
  author_email: string;
  author_role: string;
  body: string;
  created_at: string;
};

export type ClaimSummary = {
  id: number;
  project_id: number;
  causing_org: string;
  subject_kind: string;
  subject_ref: string;
  rfc_drawing_id: number | null;
  permit_id: number | null;
  idle_event_id: number | null;
  opened_at: string;
  impact_days: number;
  cod_shift_days: number;
  impact_value: number | null;
  statement_of_facts: string | null;
  approval_id: number | null;
  status: string;
  finalized_at: string | null;
  communications: Array<{
    ts: string;
    from: string;
    org: string;
    body: string;
    mentions: string[];
  }>;
};

export type FactorVector = {
  schedule: number;
  rfc: number;
  permit: number;
  long_lead: number;
  field_idle: number;
};
