"use client";

/**
 * CEO Heatmap.
 *
 * Plots every active activity at (Risk Impact, Net Exposure) on a 2-D grid
 * with the four quadrants tinted: HH red, HL/LH amber, LL green. The HH
 * quadrant is the wrap-killer — anything stuck there for ≥ 48h auto-fires
 * a Tier-3 SMS to the CEO.
 *
 * Clicking a cell slides out the Evidence drawer:
 *   • Communication Logs (Black Box)   — every Message tagged to the cell
 *   • Hashed Audit Trail               — SHA-256 per artifact + bundle hash
 *   • Subcontractor Scorecard          — per-org performance roll-up
 */

import { useEffect, useMemo, useState } from "react";
import {
  api, type EvidenceBundle, type HeatmapCell, type HeatmapReport,
} from "@/lib/api";

const PROJECT_ID = 1;

export default function HeatmapPage() {
  const [report, setReport] = useState<HeatmapReport | null>(null);
  const [selected, setSelected] = useState<HeatmapCell | null>(null);
  const [evidence, setEvidence] = useState<EvidenceBundle | null>(null);
  const [status, setStatus] = useState("");

  useEffect(() => { refresh(); }, []);

  async function refresh() {
    try { setReport(await api.heatmap(PROJECT_ID, false)); }
    catch (e) { setStatus(String(e)); }
  }

  async function open(cell: HeatmapCell) {
    setSelected(cell);
    setEvidence(null);
    try { setEvidence(await api.cellEvidence(PROJECT_ID, cell.activity_id)); }
    catch (e) { setStatus(String(e)); }
  }

  if (!report) return <p>Loading heatmap…</p>;

  return (
    <section style={{ display: "grid", gridTemplateColumns: selected ? "1fr 480px" : "1fr", gap: 16 }}>
      <div>
        <header style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <h1 style={{ marginRight: "auto" }}>CEO Heatmap</h1>
          <button onClick={refresh}>Refresh</button>
        </header>
        <p style={{ color: "#555" }}>
          Risk Impact (X) × Net Exposure (Y). HH is the wrap-killer
          quadrant — items stuck there for ≥ <strong>{report.thresholds.dwell_hours_nuclear}h</strong>{" "}
          auto-fire a Tier-3 SMS. Click any cell for the evidence drawer.
        </p>
        <Grid report={report} onOpen={open} selectedId={selected?.activity_id} />
        <p style={{ marginTop: 8, color: "#666" }}>{status}</p>

        <h3 style={{ marginTop: 24 }}>Activities</h3>
        <table style={{ borderCollapse: "collapse", width: "100%" }}>
          <thead>
            <tr>
              {["Activity", "Quadrant", "Risk", "Net exposure", "Hours in quad", "Claims", "COs"].map(h => (
                <th key={h} style={th}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {report.cells.map((c) => (
              <tr key={c.activity_id}
                  style={{ background: c.activity_id === selected?.activity_id ? "#f5f8ff" : "white", cursor: "pointer" }}
                  onClick={() => open(c)}>
                <td style={td}><code>{c.activity_id}</code></td>
                <td style={td}><QBadge q={c.quadrant} /></td>
                <td style={td}>{c.risk_impact.toFixed(1)} pts</td>
                <td style={td}>{c.net_exposure == null ? "— masked —" : `$${c.net_exposure.toLocaleString(undefined, { maximumFractionDigits: 0 })}`}</td>
                <td style={td}>{c.hours_in_quadrant.toFixed(1)}h</td>
                <td style={td}>{c.claim_ids.map(i => `#${i}`).join(", ") || "—"}</td>
                <td style={td}>{c.change_order_ids.map(i => `#${i}`).join(", ") || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {selected && (
        <EvidenceDrawer
          cell={selected}
          bundle={evidence}
          onClose={() => { setSelected(null); setEvidence(null); }}
        />
      )}
    </section>
  );
}


function Grid({
  report, onOpen, selectedId,
}: {
  report: HeatmapReport;
  onOpen: (c: HeatmapCell) => void;
  selectedId: string | undefined;
}) {
  const W = 720, H = 360, P = 36;
  const { thresholds, cells } = report;

  // Axis maxes give some headroom over thresholds.
  const maxRisk = useMemo(
    () => Math.max(thresholds.high_risk_score_points * 2, ...cells.map(c => c.risk_impact), 10),
    [report],
  );
  const maxExpo = useMemo(
    () => Math.max(
      thresholds.high_exposure_dollars * 2,
      ...cells.map(c => c.net_exposure ?? 0), 1,
    ),
    [report],
  );

  function x(v: number) { return P + (W - P * 2) * Math.min(1, v / maxRisk); }
  function y(v: number | null) {
    const val = v ?? 0;
    return H - P - (H - P * 2) * Math.min(1, val / maxExpo);
  }

  const rxThresh = x(thresholds.high_risk_score_points);
  const ryThresh = y(thresholds.high_exposure_dollars);

  return (
    <svg width={W} height={H} style={{ border: "1px solid #ccc", background: "white" }}>
      {/* Quadrant tints */}
      <rect x={rxThresh} y={P} width={W - P - rxThresh} height={ryThresh - P} fill="#fde8e8" />
      <rect x={P} y={P} width={rxThresh - P} height={ryThresh - P} fill="#fff5d6" />
      <rect x={rxThresh} y={ryThresh} width={W - P - rxThresh} height={H - P - ryThresh} fill="#fff5d6" />
      <rect x={P} y={ryThresh} width={rxThresh - P} height={H - P - ryThresh} fill="#e8fde8" />

      {/* Threshold guides */}
      <line x1={rxThresh} y1={P} x2={rxThresh} y2={H - P} stroke="#888" strokeDasharray="4 3" />
      <line x1={P} y1={ryThresh} x2={W - P} y2={ryThresh} stroke="#888" strokeDasharray="4 3" />

      {/* Axes */}
      <line x1={P} y1={H - P} x2={W - P} y2={H - P} stroke="#333" />
      <line x1={P} y1={P} x2={P} y2={H - P} stroke="#333" />
      <text x={W / 2} y={H - 8} textAnchor="middle" fontSize="12">Risk Impact (score points)</text>
      <text x={12} y={H / 2} textAnchor="middle" fontSize="12" transform={`rotate(-90 12 ${H / 2})`}>Net Exposure ($)</text>

      {/* Quadrant labels */}
      <text x={rxThresh + 8} y={P + 18} fill="#a31f1f" fontWeight={700} fontSize="11">HH · wrap-killer</text>
      <text x={P + 8} y={P + 18} fill="#a35d1f" fontWeight={600} fontSize="11">HL · de-risked drag</text>
      <text x={rxThresh + 8} y={H - P - 8} fill="#a35d1f" fontWeight={600} fontSize="11">LH · write-down risk</text>
      <text x={P + 8} y={H - P - 8} fill="#1f7a32" fontWeight={600} fontSize="11">LL · healthy</text>

      {/* Cells */}
      {cells.map((c) => {
        const cx = x(c.risk_impact);
        const cy = y(c.net_exposure ?? 0);
        const isSel = c.activity_id === selectedId;
        const dwellRed = c.quadrant === "HH" && c.hours_in_quadrant >= report.thresholds.dwell_hours_nuclear;
        return (
          <g key={c.activity_id}
             onClick={() => onOpen(c)}
             style={{ cursor: "pointer" }}>
            <circle cx={cx} cy={cy} r={isSel ? 9 : 7}
              fill={dwellRed ? "#a31f1f" : c.quadrant === "HH" ? "#d97a7a" : "#444"}
              stroke="#000" strokeWidth={isSel ? 2 : 0.5} />
            <text x={cx + 10} y={cy + 4} fontSize="11">{c.activity_id}</text>
          </g>
        );
      })}
    </svg>
  );
}


function QBadge({ q }: { q: string }) {
  const color = q === "HH" ? "#a31f1f"
              : q === "HL" || q === "LH" ? "#a35d1f"
              : "#1f7a32";
  return (
    <span style={{ background: color, color: "white", padding: "1px 6px", borderRadius: 3, fontSize: 11 }}>
      {q}
    </span>
  );
}


function EvidenceDrawer({
  cell, bundle, onClose,
}: {
  cell: HeatmapCell;
  bundle: EvidenceBundle | null;
  onClose: () => void;
}) {
  return (
    <aside style={{
      borderLeft: "4px solid #1d4fa3", padding: 16, background: "#fcfcff",
      maxHeight: "calc(100vh - 100px)", overflowY: "auto",
    }}>
      <header style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <h2 style={{ margin: 0, marginRight: "auto" }}>
          Evidence — <code>{cell.activity_id}</code>
        </h2>
        <button onClick={onClose}>Close</button>
      </header>
      <p style={{ fontSize: 12, color: "#666" }}>
        Risk {cell.risk_impact.toFixed(1)} pts · Net exposure{" "}
        {cell.net_exposure == null ? "— masked —" : `$${cell.net_exposure.toLocaleString(undefined, { maximumFractionDigits: 0 })}`}{" "}
        · in <strong>{cell.quadrant}</strong> for {cell.hours_in_quadrant.toFixed(1)}h
      </p>

      {!bundle && <p>Loading evidence…</p>}
      {bundle && (
        <>
          <p style={{ fontSize: 11, color: "#444" }}>
            Bundle hash: <code>{bundle.bundle_hash.slice(0, 16)}…</code>{" "}
            · generated {bundle.generated_at}
          </p>

          <h3>Communication Logs ({bundle.communications.length})</h3>
          {bundle.communications.length === 0 && <p style={{ color: "#888" }}>No tagged messages.</p>}
          <ul style={{ paddingLeft: 16, marginTop: 0 }}>
            {bundle.communications.map((m, i) => (
              <li key={i} style={{ marginBottom: 6 }}>
                <strong>{m.timestamp}</strong>{" "}
                <code>{m.sender_email}</code> ({m.sender_org})
                {m.activity_id && <Pill>activity={m.activity_id}</Pill>}
                {m.rfc_drawing_id != null && <Pill>rfc#{m.rfc_drawing_id}</Pill>}
                {m.permit_id != null && <Pill>permit#{m.permit_id}</Pill>}
                <div style={{ fontSize: 13 }}>{m.body}</div>
              </li>
            ))}
          </ul>

          <h3>Hashed Audit Trail ({bundle.audit_trail.length})</h3>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead><tr>{["When", "Kind", "ID", "Actor", "SHA-256"].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
            <tbody>
              {bundle.audit_trail.map((a) => (
                <tr key={`${a.kind}-${a.id}`}>
                  <td style={td}>{a.occurred_at}</td>
                  <td style={td}>{a.kind}</td>
                  <td style={td}>#{a.id}</td>
                  <td style={td}>{a.actor ?? "—"}</td>
                  <td style={{ ...td, fontFamily: "monospace" }}>{a.sha256.slice(0, 16)}…</td>
                </tr>
              ))}
            </tbody>
          </table>

          <h3>Subcontractor Scorecard ({bundle.scorecard.length})</h3>
          {bundle.scorecard.length === 0 && <p style={{ color: "#888" }}>No org rolled up yet.</p>}
          {bundle.scorecard.map((s) => (
            <div key={`${s.org}-${s.role}`} style={{ border: "1px solid #ddd", padding: 8, marginBottom: 6, borderRadius: 4 }}>
              <div style={{ display: "flex", gap: 6, alignItems: "baseline" }}>
                <strong>{s.org}</strong>
                <Pill>{s.role}</Pill>
                {s.double_count_flagged > 0 && (
                  <span style={{ color: "#a31f1f", fontSize: 11 }}>· {s.double_count_flagged} double-count flag(s)</span>
                )}
              </div>
              <div style={{ fontSize: 12 }}>
                RFCs: {s.rfc_on_time}/{s.rfc_total} on time ({s.rfc_on_time_pct}%)
                {" · "}Claims: {s.claim_count} · gross{" "}
                {s.claim_gross_total == null ? "— masked —" : `$${s.claim_gross_total.toLocaleString(undefined, { maximumFractionDigits: 0 })}`}
                {s.claim_net_total != null && <>{" · "}net ${s.claim_net_total.toLocaleString(undefined, { maximumFractionDigits: 0 })}</>}
                {" · "}COs: {s.co_approved}/{s.co_count} approved ({s.co_approval_pct}%)
              </div>
            </div>
          ))}
        </>
      )}
    </aside>
  );
}


function Pill({ children }: { children: React.ReactNode }) {
  return (
    <span style={{
      background: "#eef", color: "#225", padding: "1px 6px",
      marginLeft: 6, borderRadius: 3, fontSize: 11,
    }}>
      {children}
    </span>
  );
}

const th: React.CSSProperties = {
  border: "1px solid #ccc", padding: "4px 8px", background: "#f6f6f6",
  fontSize: 12, textAlign: "left",
};
const td: React.CSSProperties = { border: "1px solid #eee", padding: "4px 8px", fontSize: 12 };
