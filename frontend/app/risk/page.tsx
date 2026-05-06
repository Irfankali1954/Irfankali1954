"use client";

/**
 * Wrap Risk dashboard with the *Simulate RFC Miss* control.
 *
 * One-click flow:
 *   1. Seed demo project + drawings (idempotent).
 *   2. Pick a drawing + days overdue + crew size.
 *   3. POST /risk/projects/1/simulate-rfc-miss → backdate due date,
 *      open IdleEvent, recompute WrapScore, return before/after.
 *   4. Render the score swing and per-factor delta so you can *see* the
 *      Engineering→Field→Score cross-pollination math.
 */

import { useEffect, useState } from "react";
import { api, type FactorVector } from "@/lib/api";

const PROJECT_ID = 1;

type Sim = Awaited<ReturnType<typeof api.simulateRfcMiss>>;
type Score = Awaited<ReturnType<typeof api.wrapScore>>;

export default function RiskPage() {
  const [score, setScore] = useState<Score | null>(null);
  const [drawings, setDrawings] = useState<{ id: number; drawing_no: string }[]>([]);
  const [permits, setPermits] = useState<{ id: number; permit_type: string }[]>([]);
  const [drawingId, setDrawingId] = useState<number | null>(null);
  const [permitId, setPermitId] = useState<number | null>(null);
  const [daysOverdue, setDaysOverdue] = useState(7);
  const [idleCrew, setIdleCrew] = useState(12);
  const [crewRate, setCrewRate] = useState(120);
  const [sim, setSim] = useState<Sim | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.wrapScore(PROJECT_ID).then(setScore).catch(() => {});
  }, []);

  async function seed() {
    try {
      const r = await api.seedDemo(PROJECT_ID);
      setDrawings(r.rfc_drawings);
      setPermits(r.permits);
      if (r.rfc_drawings[0]) setDrawingId(r.rfc_drawings[0].id);
      if (r.permits[0]) setPermitId(r.permits[0].id);
    } catch (e) { setErr(String(e)); }
  }

  async function simulatePermit() {
    if (permitId == null) return;
    setBusy(true); setErr("");
    try {
      const r = await api.simulatePermitDelay(PROJECT_ID, {
        permit_id: permitId,
        days_overdue: daysOverdue,
        idle_crew: idleCrew,
        crew_burdened_rate: crewRate,
      });
      setSim(r);
      api.wrapScore(PROJECT_ID).then(setScore).catch(() => {});
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function simulate() {
    if (drawingId == null) return;
    setBusy(true); setErr("");
    try {
      const r = await api.simulateRfcMiss(PROJECT_ID, {
        rfc_drawing_id: drawingId,
        days_overdue: daysOverdue,
        idle_crew: idleCrew,
        crew_burdened_rate: crewRate,
      });
      setSim(r);
      api.wrapScore(PROJECT_ID).then(setScore).catch(() => {});
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <h1>Master-Wrap Risk Engine</h1>

      {score && (
        <div style={{ display: "flex", gap: 32, alignItems: "baseline" }}>
          <h2 style={{ fontSize: 56, margin: 0 }}>{score.score.toFixed(1)}</h2>
          <span>P(hit COD) — composite score, recomputed on every RFC miss, daily log, or ERP sync.</span>
        </div>
      )}

      <hr style={{ margin: "24px 0" }} />
      <h3>Simulate Delay</h3>
      <p>
        No real Oracle/P6 data yet? Click <em>Seed demo</em>, then trigger an RFC
        miss to watch the score swing in real time.
      </p>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
        <button onClick={seed}>Seed demo</button>
        <select
          value={drawingId ?? ""}
          onChange={(e) => setDrawingId(Number(e.target.value))}
          disabled={drawings.length === 0}
        >
          {drawings.length === 0 && <option value="">— seed first —</option>}
          {drawings.map((d) => (
            <option key={d.id} value={d.id}>{d.drawing_no}</option>
          ))}
        </select>
        <label>Days overdue
          <input type="number" min={1} value={daysOverdue}
            onChange={(e) => setDaysOverdue(Number(e.target.value))} style={{ width: 60, marginLeft: 6 }} />
        </label>
        <label>Idle crew
          <input type="number" min={0} value={idleCrew}
            onChange={(e) => setIdleCrew(Number(e.target.value))} style={{ width: 60, marginLeft: 6 }} />
        </label>
        <label>Crew rate $/h
          <input type="number" min={0} value={crewRate}
            onChange={(e) => setCrewRate(Number(e.target.value))} style={{ width: 80, marginLeft: 6 }} />
        </label>
        <button onClick={simulate} disabled={busy || drawingId == null}>
          {busy ? "Simulating…" : "Simulate RFC miss"}
        </button>
      </div>

      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center", marginTop: 12 }}>
        <select
          value={permitId ?? ""}
          onChange={(e) => setPermitId(Number(e.target.value))}
          disabled={permits.length === 0}
        >
          {permits.length === 0 && <option value="">— seed first —</option>}
          {permits.map((p) => (
            <option key={p.id} value={p.id}>{p.permit_type}</option>
          ))}
        </select>
        <button onClick={simulatePermit} disabled={busy || permitId == null}>
          {busy ? "Simulating…" : "Simulate Permit Delay"}
        </button>
      </div>

      {err && <pre style={{ color: "crimson" }}>{err}</pre>}

      {sim && (
        <div style={{ marginTop: 24 }}>
          <h3>Result</h3>
          <p>
            Score: <strong>{sim.before_score.toFixed(1)}</strong> →{" "}
            <strong>{sim.after_score.toFixed(1)}</strong>{" "}
            <span style={{ color: sim.delta < 0 ? "crimson" : "green" }}>
              ({sim.delta >= 0 ? "+" : ""}{sim.delta.toFixed(1)})
            </span>
          </p>
          {sim.claim_id != null && (
            <p style={{ background: "#fff7d6", padding: 10, border: "1px solid #e5d585", borderRadius: 4 }}>
              Auto-drafted Delay-Claim <strong>#{sim.claim_id}</strong> with CFO approval{" "}
              <strong>#{sim.approval_id}</strong> pending.{" "}
              <a href="/claims">Review in Claims →</a>
            </p>
          )}
          <p>
            Field idle cost generated:{" "}
            {sim.idle_cost == null ? <em>masked by visibility policy</em> : `$${sim.idle_cost.toLocaleString(undefined, { maximumFractionDigits: 0 })}`}
          </p>
          <table style={{ borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th style={cell}>Factor</th><th style={cell}>Before</th><th style={cell}>After</th><th style={cell}>Δ</th>
              </tr>
            </thead>
            <tbody>
              {(Object.keys(sim.factors_before) as Array<keyof FactorVector>).map((k) => (
                <tr key={k}>
                  <td style={cell}>{k}</td>
                  <td style={cell}>{sim.factors_before[k].toFixed(3)}</td>
                  <td style={cell}>{sim.factors_after[k].toFixed(3)}</td>
                  <td style={{ ...cell, color: sim.factors_after[k] < sim.factors_before[k] ? "crimson" : "#333" }}>
                    {(sim.factors_after[k] - sim.factors_before[k]).toFixed(3)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

const cell: React.CSSProperties = { border: "1px solid #ddd", padding: "6px 12px" };
