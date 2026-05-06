"use client";

/**
 * Delay-Claims surface.
 *
 * Drives the full Auto-Packet workflow:
 *   1. List drafted claims for a project (auto-harvested by simulate_rfc_miss
 *      or by POST /claims/by-idle-event/{id}).
 *   2. Render the persisted Statement of Facts inline.
 *   3. CFO approve/reject the linked GatekeeperApproval.
 *   4. Once approved, "Finalize" flips status draft → filed.
 *   5. Open the printable HTML Defense Packet in a new tab.
 *
 * Financial fields render as `— masked —` for any role that lacks
 * DELAY_CLAIM_VALUE in the CFO's Visibility Policy.
 */

import { useEffect, useState } from "react";
import { api, type ClaimSummary, type Role } from "@/lib/api";
import { CommentsThread } from "@/components/CommentsThread";

const PROJECT_ID = 1;

export default function ClaimsPage() {
  const [claims, setClaims] = useState<ClaimSummary[]>([]);
  const [selected, setSelected] = useState<ClaimSummary | null>(null);
  const [packet, setPacket] = useState<string>("");
  const [status, setStatus] = useState<string>("");
  const [role, setRole] = useState<Role | null>(null);

  useEffect(() => {
    setRole((typeof window !== "undefined"
      ? (window.localStorage.getItem("epc_role") as Role | null)
      : null));
    refresh();
  }, []);

  async function refresh() {
    try {
      const rows = await api.listClaims(PROJECT_ID);
      setClaims(rows);
      if (rows.length > 0 && !selected) loadClaim(rows[0]);
    } catch (e) { setStatus(String(e)); }
  }

  async function loadClaim(c: ClaimSummary) {
    setSelected(c);
    try {
      setPacket(await api.fetchPacket(c.id, "md"));
    } catch (e) {
      setPacket(`(packet load failed: ${e})`);
    }
  }

  async function decide(decision: "approve" | "reject") {
    if (!selected || selected.approval_id == null) return;
    setStatus(`${decision}ing…`);
    try {
      await api.approveApproval(selected.approval_id, decision, `${decision} via UI`);
      setStatus(`approval ${decision}d.`);
      await refresh();
      if (selected) {
        const fresh = await api.getClaim(selected.id);
        setSelected(fresh);
        setPacket(await api.fetchPacket(fresh.id, "md"));
      }
    } catch (e) { setStatus(String(e)); }
  }

  async function finalize() {
    if (!selected) return;
    setStatus("finalizing…");
    try {
      const updated = await api.finalizeClaim(selected.id);
      setSelected(updated);
      setStatus(`status: ${updated.status}`);
      refresh();
    } catch (e) { setStatus(String(e)); }
  }

  const isCfo = role === "cfo";
  const canFinalize = role === "cfo" || role === "project_director" || role === "epc_manager";

  return (
    <section style={{ display: "grid", gridTemplateColumns: "320px 1fr", gap: 24 }}>
      <aside>
        <h2>Claims</h2>
        <p style={{ color: "#666", fontSize: 13 }}>
          Auto-harvested when the Wrap Risk simulator opens an Idle Event.
        </p>
        {claims.length === 0 && <p style={{ color: "#888" }}>None yet — run a Simulate RFC miss.</p>}
        <ul style={{ listStyle: "none", padding: 0 }}>
          {claims.map((c) => (
            <li key={c.id}
                onClick={() => loadClaim(c)}
                style={{
                  padding: 10, marginBottom: 6, cursor: "pointer",
                  border: "1px solid #ddd",
                  borderLeft: `4px solid ${statusColor(c.status)}`,
                  background: selected?.id === c.id ? "#f5f8ff" : "white",
                }}>
              <div><strong>#{c.id}</strong> · {c.subject_kind}: {c.subject_ref}</div>
              <div style={{ fontSize: 12, color: "#555" }}>{c.causing_org}</div>
              <div style={{ fontSize: 11, color: "#777" }}>
                status: <em>{c.status}</em> · COD slip: {c.cod_shift_days.toFixed(1)}d
              </div>
            </li>
          ))}
        </ul>
      </aside>

      <article>
        {selected ? (
          <>
            <header style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
              <h1 style={{ margin: 0 }}>Claim #{selected.id}</h1>
              <span style={{ background: statusColor(selected.status), color: "white", padding: "2px 8px", borderRadius: 4 }}>
                {selected.status}
              </span>
              <a href={api.packetUrl(selected.id, "html")} target="_blank" rel="noreferrer"
                 style={{ marginLeft: "auto" }}>Open printable packet ↗</a>
            </header>

            <p>
              Versus <strong>{selected.causing_org}</strong> · subject{" "}
              <code>{selected.subject_ref}</code>
              {selected.linked_activity_id && <> · activity <code>{selected.linked_activity_id}</code></>}
              {" "}· COD slip{" "}
              <strong>{selected.cod_shift_days.toFixed(1)} days</strong>
            </p>

            <p>
              Gross impact{" "}
              <strong>
                {selected.impact_value == null
                  ? "— masked —"
                  : `$${selected.impact_value.toLocaleString(undefined, { maximumFractionDigits: 0 })}`}
              </strong>
              {selected.co_offset_value != null && selected.co_offset_value > 0 && (
                <>
                  {" "}· CO offset{" "}
                  <strong>{`$${selected.co_offset_value.toLocaleString(undefined, { maximumFractionDigits: 0 })}`}</strong>
                  {" "}· <span style={{ color: "#1f7a32" }}>net <strong>{`$${(selected.net_impact_value ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}`}</strong></span>
                </>
              )}
              {selected.double_count_flag && (
                <span style={{
                  marginLeft: 8, background: "#a31f1f", color: "white",
                  padding: "1px 8px", borderRadius: 3, fontSize: 11,
                }}>
                  DOUBLE-COUNT FLAGGED
                </span>
              )}
            </p>

            <div style={{ display: "flex", gap: 8, margin: "12px 0" }}>
              <button disabled={!isCfo || selected.status !== "draft"} onClick={() => decide("approve")}>
                CFO approve
              </button>
              <button disabled={!isCfo || selected.status !== "draft"} onClick={() => decide("reject")}>
                CFO reject
              </button>
              <button disabled={!canFinalize} onClick={finalize}>Finalize → filed</button>
              <span style={{ marginLeft: 8 }}>{status}</span>
            </div>

            <h3>Statement of Facts (persisted)</h3>
            <pre style={{
              whiteSpace: "pre-wrap", background: "#fafafa", padding: 16,
              border: "1px solid #eee", borderRadius: 4, fontFamily: "Georgia, serif",
            }}>
              {packet || selected.statement_of_facts || "(no SoF rendered)"}
            </pre>

            <CommentsThread kind="claim" id={selected.id} />
          </>
        ) : (
          <p>Select a claim from the list.</p>
        )}
      </article>
    </section>
  );
}

function statusColor(s: string): string {
  switch (s) {
    case "draft":    return "#888";
    case "approved": return "#1f7a32";
    case "rejected": return "#a31f1f";
    case "filed":    return "#1d4fa3";
    default:         return "#444";
  }
}
