"use client";

/**
 * CFO Net Exposure dashboard — Convergence of Truth.
 *
 * Per-activity reconciliation of gross Delay-Claim damages vs. approved
 * Change Order recovery. The `net_exposure` column is what the bank
 * auditor will reconcile against the books at project close-out — if it
 * exceeds the gross claim, we are double-counting; if it is fully de-risked
 * (recovery ≥ gross), we should not be filing the claim at all.
 */

import { useEffect, useState } from "react";
import { api, type NetExposureReport, type Role } from "@/lib/api";

const PROJECT_ID = 1;

export default function ExposurePage() {
  const [report, setReport] = useState<NetExposureReport | null>(null);
  const [status, setStatus] = useState("");
  const [role, setRole] = useState<Role | null>(null);

  useEffect(() => {
    setRole((typeof window !== "undefined"
      ? (window.localStorage.getItem("epc_role") as Role | null)
      : null));
    refresh();
  }, []);

  async function refresh() {
    try { setReport(await api.netExposure(PROJECT_ID)); }
    catch (e) { setStatus(String(e)); }
  }

  async function reconcile() {
    if (role !== "cfo") return;
    setStatus("reconciling…");
    try {
      const r = await api.reconcileExposure(PROJECT_ID);
      setStatus(`reconciled ${r.activities_reconciled} activity row(s).`);
      refresh();
    } catch (e) { setStatus(String(e)); }
  }

  if (!report) return <p>Loading exposure…</p>;
  const { items, totals, money_visible } = report;
  const isCfo = role === "cfo";

  return (
    <section>
      <header style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <h1 style={{ marginRight: "auto" }}>Net Exposure</h1>
        <button onClick={refresh}>Refresh</button>
        {isCfo && <button onClick={reconcile}>Reconcile all activities</button>}
      </header>
      <p style={{ color: "#555" }}>
        For every activity carrying a claim or an approved Change Order:
        Net Exposure = <strong>Σ Gross Claim − Σ Approved CO Recovery</strong>,
        clamped at zero. Activities flagged <Badge color="#a31f1f">DOUBLE-COUNT</Badge>{" "}
        carry both an open claim AND an approved CO on the same scope —
        confirm with counsel before filing.
      </p>
      {!money_visible && (
        <p style={{ color: "#a35d1f" }}>
          Financial fields are masked for your role. Contact the CFO to
          adjust the Visibility Policy if you need them.
        </p>
      )}

      {/* Totals summary */}
      <section style={{ display: "flex", gap: 24, padding: 16, background: "#f6f8ff", borderRadius: 4, marginBottom: 16 }}>
        <Stat label="Gross claims" value={fmtMoney(totals.gross_claim_impact)} />
        <Stat label="Approved CO recovery" value={fmtMoney(totals.approved_co_recovery)} />
        <Stat label="Net exposure to wrap" value={fmtMoney(totals.net_exposure)} bold />
        <Stat label="Activities at double-count risk" value={String(totals.double_count_activities)} />
      </section>

      <table style={{ borderCollapse: "collapse", width: "100%" }}>
        <thead>
          <tr>
            {["Activity", "Risk Impact", "Gross Claims", "Approved CO Recovery", "Net Exposure", "Claims", "COs", "Status"].map((h) => (
              <th key={h} style={th}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {items.map((r) => (
            <tr key={r.activity_id} style={{ background: rowBg(r) }}>
              <td style={td}><code>{r.activity_id}</code></td>
              <td style={td} title={r.risk_breakdown
                ? `schedule ${(r.risk_breakdown.schedule * 100).toFixed(1)}%, rfc ${(r.risk_breakdown.rfc * 100).toFixed(1)}%, permit ${(r.risk_breakdown.permit * 100).toFixed(1)}%, idle ${(r.risk_breakdown.idle * 100).toFixed(1)}%`
                : ""}>
                <strong style={{ color: r.risk_impact >= 5 ? "#a31f1f" : "#333" }}>
                  {r.risk_impact.toFixed(1)} pts
                </strong>
              </td>
              <td style={td}>{fmtMoney(r.gross_claim_impact)}</td>
              <td style={td}>{fmtMoney(r.approved_co_recovery)}</td>
              <td style={td}><strong>{fmtMoney(r.net_exposure)}</strong></td>
              <td style={td}>{r.claim_ids.map((i) => `#${i}`).join(", ") || "—"}</td>
              <td style={td}>{r.change_order_ids.map((i) => `#${i}`).join(", ") || "—"}</td>
              <td style={td}>
                {r.fully_de_risked && <Badge color="#1f7a32">DE-RISKED</Badge>}
                {r.double_count_risk && <Badge color="#a31f1f">DOUBLE-COUNT</Badge>}
                {!r.double_count_risk && !r.fully_de_risked && <span style={{ color: "#666" }}>open</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p style={{ marginTop: 8, color: "#666" }}>{status}</p>
    </section>
  );
}

function Stat({ label, value, bold }: { label: string; value: string; bold?: boolean }) {
  return (
    <div>
      <div style={{ fontSize: 11, color: "#555", textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: bold ? 700 : 500 }}>{value}</div>
    </div>
  );
}

function Badge({ color, children }: { color: string; children: React.ReactNode }) {
  return (
    <span style={{ background: color, color: "white", padding: "1px 6px", borderRadius: 3, fontSize: 11, marginRight: 4 }}>
      {children}
    </span>
  );
}

function fmtMoney(v: number | null | undefined) {
  if (v == null) return "— masked —";
  return `$${Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

function rowBg(r: { double_count_risk: boolean; fully_de_risked: boolean }) {
  if (r.double_count_risk && !r.fully_de_risked) return "#fde8e8";
  if (r.fully_de_risked) return "#e8fde8";
  return "white";
}

const th: React.CSSProperties = { border: "1px solid #ccc", padding: "6px 10px", background: "#f6f6f6", fontSize: 12, textAlign: "left" };
const td: React.CSSProperties = { border: "1px solid #eee", padding: "4px 10px", fontSize: 13 };
