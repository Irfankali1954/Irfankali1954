"use client";

/**
 * Change Order Sentinel — list + draft + lifecycle controls.
 *
 * The aging snapshot drives row colour:
 *   green  — ok
 *   amber  — approaching deadline (< 24h notice or < 72h claim)
 *   red    — TIME BAR (deadline missed)
 *
 * The CFO sees the markup column and can edit it inline; everyone else sees
 * "— masked —" because the API stripped the field at the boundary.
 */

import { useEffect, useMemo, useState } from "react";
import {
  api, type AgingItem, type ChangeOrderRow, type ChangeOrderDraft, type Role,
} from "@/lib/api";

const PROJECT_ID = 1;

export default function ChangeOrdersPage() {
  const [rows, setRows] = useState<ChangeOrderRow[]>([]);
  const [aging, setAging] = useState<Record<number, AgingItem>>({});
  const [role, setRole] = useState<Role | null>(null);
  const [status, setStatus] = useState("");
  const [showDraft, setShowDraft] = useState(false);

  useEffect(() => {
    setRole((typeof window !== "undefined"
      ? (window.localStorage.getItem("epc_role") as Role | null)
      : null));
    refresh();
  }, []);

  async function refresh() {
    try {
      const [list, snap] = await Promise.all([
        api.listChangeOrders(PROJECT_ID),
        api.sentinelSnapshot(PROJECT_ID),
      ]);
      setRows(list);
      const map: Record<number, AgingItem> = {};
      for (const i of snap.items) map[i.change_order_id] = i;
      setAging(map);
    } catch (e) { setStatus(String(e)); }
  }

  async function runScan() {
    setStatus("scanning…");
    try {
      const r = await api.sentinelScan(PROJECT_ID);
      setStatus(`scan complete · ${r.notifications_fired} notification(s) fired`);
      refresh();
    } catch (e) { setStatus(String(e)); }
  }

  const isCfo = role === "cfo";

  return (
    <section>
      <header style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <h1 style={{ marginRight: "auto" }}>Change Order Sentinel</h1>
        <button onClick={runScan}>Run sentinel scan</button>
        <button onClick={() => setShowDraft((s) => !s)}>{showDraft ? "Cancel" : "Draft new CO"}</button>
      </header>
      <p style={{ color: "#555" }}>
        The clock starts at <strong>discovery</strong>. Default notice
        deadline is <strong>7 days</strong>; default claim deadline is{" "}
        <strong>21 days</strong>. A missed deadline forfeits recovery —
        the bus texts the CEO when that happens, or when an aging CO sits
        on the critical path.
      </p>
      {showDraft && <DraftForm onCreated={() => { setShowDraft(false); refresh(); }} />}

      <table style={{ borderCollapse: "collapse", width: "100%", marginTop: 16 }}>
        <thead>
          <tr>
            {["#", "Title", "Activity", "CP?", "Status", "Severity", "Deadline", "Direct", "Markup %", "Total", ""].map((h) => (
              <th key={h} style={th}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const a = aging[r.id];
            return (
              <tr key={r.id} style={{ background: rowBg(a?.severity) }}>
                <td style={td}>{r.co_number}</td>
                <td style={td}>{r.title}</td>
                <td style={td}><code>{r.linked_activity_id}</code></td>
                <td style={td}>{r.on_critical_path ? "🔴" : "—"}</td>
                <td style={td}>{r.status}</td>
                <td style={td}><Severity a={a} /></td>
                <td style={td}>{a ? countdown(a.seconds_remaining) : "—"}</td>
                <td style={td}>{fmtMoney(r.direct_cost)}</td>
                <td style={td}>
                  {isCfo ? (
                    <MarkupCell row={r} onUpdated={refresh} />
                  ) : (r.markup_pct == null ? "—" : `${r.markup_pct}%`)}
                </td>
                <td style={td}>{fmtMoney(r.proposed_value)}</td>
                <td style={td}>
                  <Lifecycle row={r} role={role} onUpdated={refresh} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p style={{ marginTop: 8, color: "#666" }}>{status}</p>
    </section>
  );
}

function DraftForm({ onCreated }: { onCreated: () => void }) {
  const [d, setD] = useState<ChangeOrderDraft>({
    project_id: PROJECT_ID, co_number: "", title: "",
    originator_org: "LocalCivilSub",
    linked_activity_id: "CIV-1040",
    direct_cost: 100_000,
    notice_period_days: 7, claim_period_days: 21,
    contract_clause: "GC-12.4",
  });
  const [err, setErr] = useState("");

  async function submit() {
    try { await api.draftChangeOrder(d); onCreated(); }
    catch (e) { setErr(String(e)); }
  }

  return (
    <section style={{ background: "#f8f8ff", padding: 12, border: "1px solid #ccd", borderRadius: 4, margin: "12px 0" }}>
      <h3 style={{ marginTop: 0 }}>Draft Change Order</h3>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
        <input placeholder="CO number" value={d.co_number} onChange={(e) => setD({...d, co_number: e.target.value})} />
        <input placeholder="Title" value={d.title} onChange={(e) => setD({...d, title: e.target.value})} />
        <input placeholder="Linked activity ID" value={d.linked_activity_id} onChange={(e) => setD({...d, linked_activity_id: e.target.value})} />
        <input placeholder="Originator org" value={d.originator_org} onChange={(e) => setD({...d, originator_org: e.target.value})} />
        <input placeholder="Contract clause" value={d.contract_clause ?? ""} onChange={(e) => setD({...d, contract_clause: e.target.value})} />
        <input type="number" placeholder="Direct cost" value={d.direct_cost ?? 0} onChange={(e) => setD({...d, direct_cost: Number(e.target.value)})} />
        <label>Notice period (d)
          <input type="number" value={d.notice_period_days ?? 7} onChange={(e) => setD({...d, notice_period_days: Number(e.target.value)})} style={{ marginLeft: 6, width: 60 }} />
        </label>
        <label>Claim period (d)
          <input type="number" value={d.claim_period_days ?? 21} onChange={(e) => setD({...d, claim_period_days: Number(e.target.value)})} style={{ marginLeft: 6, width: 60 }} />
        </label>
      </div>
      <div style={{ marginTop: 8 }}>
        <button onClick={submit} disabled={!d.co_number || !d.title}>Create (start clock)</button>
        <span style={{ color: "crimson", marginLeft: 12 }}>{err}</span>
      </div>
    </section>
  );
}

function MarkupCell({ row, onUpdated }: { row: ChangeOrderRow; onUpdated: () => void }) {
  const [pct, setPct] = useState<number>(row.markup_pct ?? 0);
  const [busy, setBusy] = useState(false);
  async function save() {
    setBusy(true);
    try { await api.setCOMarkup(row.id, pct); onUpdated(); }
    finally { setBusy(false); }
  }
  return (
    <span>
      <input type="number" min={0} max={100} value={pct}
        onChange={(e) => setPct(Number(e.target.value))}
        style={{ width: 50 }} /> %{" "}
      <button onClick={save} disabled={busy}>save</button>
    </span>
  );
}

function Lifecycle({
  row, role, onUpdated,
}: { row: ChangeOrderRow; role: Role | null; onUpdated: () => void }) {
  const [busy, setBusy] = useState(false);

  async function step(fn: () => Promise<unknown>) {
    setBusy(true);
    try { await fn(); onUpdated(); }
    catch (e) { alert(String(e)); }
    finally { setBusy(false); }
  }

  if (row.status === "pending_notice") {
    return (
      <button disabled={busy} onClick={() => step(() => api.sendCONotice(row.id))}>
        Send notice
      </button>
    );
  }
  if (row.status === "notice_sent") {
    return (
      <button disabled={busy} onClick={() => step(() => api.fileCOClaim(row.id))}>
        File claim
      </button>
    );
  }
  if (row.status === "claim_filed") {
    return role === "cfo" ? (
      <button disabled={busy} onClick={() => step(() => api.approveCO(row.id))}>
        CFO approve
      </button>
    ) : <span style={{ color: "#666" }}>awaiting CFO</span>;
  }
  return <span>{row.status}</span>;
}

function Severity({ a }: { a: AgingItem | undefined }) {
  if (!a) return <span>—</span>;
  const color =
    a.severity === "missed" ? "#a31f1f" :
    a.severity === "approaching" ? "#a35d1f" : "#1f7a32";
  const label =
    a.severity === "missed" ? "TIME BAR" :
    a.severity === "approaching" ? "AGING" : "ok";
  return (
    <span style={{ background: color, color: "white", padding: "1px 6px", borderRadius: 3, fontSize: 11 }}>
      {label} · {a.deadline_kind}
    </span>
  );
}

function rowBg(severity: string | undefined) {
  if (severity === "missed") return "#fde8e8";
  if (severity === "approaching") return "#fff5d6";
  return "white";
}

function countdown(seconds: number) {
  if (seconds <= 0) return <strong style={{ color: "#a31f1f" }}>missed</strong>;
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  return <span>{days > 0 ? `${days}d ` : ""}{hours}h</span>;
}

function fmtMoney(v: number | null | undefined) {
  if (v == null) return "— masked —";
  return `$${Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

const th: React.CSSProperties = { border: "1px solid #ccc", padding: "4px 8px", background: "#f6f6f6", fontSize: 12, textAlign: "left" };
const td: React.CSSProperties = { border: "1px solid #eee", padding: "4px 8px", verticalAlign: "middle", fontSize: 13 };
