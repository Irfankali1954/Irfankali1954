"use client";

/**
 * CFO Senior Alert List editor.
 *
 * Tier 3 ("Nuclear") fan-out only reaches the people on this list. The CFO
 * is the sole role that can mutate it — server enforces via the
 * SENIOR_ALERT_LIST_WRITE permission.
 */

import { useEffect, useState } from "react";
import { api, type Recipient, type RecipientInput, type Role } from "@/lib/api";

const BLANK: RecipientInput = {
  name: "", role_label: "CEO", email: "", phone: "",
  tiers: ["tier_3"], active: true,
};

export default function AlertsPage() {
  const [rows, setRows] = useState<Recipient[]>([]);
  const [draft, setDraft] = useState<RecipientInput>(BLANK);
  const [status, setStatus] = useState("");
  const [role, setRole] = useState<Role | null>(null);

  useEffect(() => {
    setRole((typeof window !== "undefined"
      ? (window.localStorage.getItem("epc_role") as Role | null)
      : null));
    refresh();
  }, []);

  async function refresh() {
    try { setRows(await api.listRecipients()); }
    catch (e) { setStatus(`load: ${e}`); }
  }

  async function add() {
    if (!draft.name) return;
    try {
      await api.addRecipient({
        ...draft,
        email: draft.email || null,
        phone: draft.phone || null,
      });
      setDraft(BLANK);
      setStatus("added.");
      refresh();
    } catch (e) { setStatus(String(e)); }
  }

  async function deactivate(id: number) {
    try {
      await api.deactivateRecipient(id);
      refresh();
    } catch (e) { setStatus(String(e)); }
  }

  function toggleTier(t: "tier_1" | "tier_2" | "tier_3") {
    setDraft((d) => ({
      ...d,
      tiers: d.tiers.includes(t) ? d.tiers.filter((x) => x !== t) : [...d.tiers, t],
    }));
  }

  const isCfo = role === "cfo";

  return (
    <section>
      <h1>Senior Alert List (CFO only)</h1>
      <p>
        Tier 2 escalations email anyone with <code>tier_2</code>. Tier 3
        escalations <strong>SMS</strong> anyone with <code>tier_3</code>. Add
        the CEO to <code>tier_3</code> only — they should not receive
        every dashboard ping.
      </p>
      {!isCfo && (
        <p style={{ color: "#a00" }}>
          You are signed in as <code>{role}</code>. Saves are disabled —
          only the CFO may mutate the list.
        </p>
      )}

      <table style={{ borderCollapse: "collapse", width: "100%", maxWidth: 980 }}>
        <thead>
          <tr>
            {["Name", "Role", "Email", "Phone", "Tiers", "Active", ""].map((h) => (
              <th key={h} style={th}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} style={{ opacity: r.active ? 1 : 0.4 }}>
              <td style={td}>{r.name}</td>
              <td style={td}>{r.role_label}</td>
              <td style={td}>{r.email ?? "—"}</td>
              <td style={td}>{r.phone ?? "—"}</td>
              <td style={td}>{r.tiers.map((t) => <Pill key={t} t={t} />)}</td>
              <td style={td}>{r.active ? "✓" : "off"}</td>
              <td style={td}>
                {r.active && (
                  <button disabled={!isCfo} onClick={() => deactivate(r.id)}>Deactivate</button>
                )}
              </td>
            </tr>
          ))}
          {/* Add row */}
          <tr>
            <td style={td}><input value={draft.name} onChange={(e) => setDraft({...draft, name: e.target.value})} placeholder="Jane Smith" /></td>
            <td style={td}><input value={draft.role_label} onChange={(e) => setDraft({...draft, role_label: e.target.value})} placeholder="CEO" /></td>
            <td style={td}><input value={draft.email ?? ""} onChange={(e) => setDraft({...draft, email: e.target.value})} placeholder="jane@lead.epc" /></td>
            <td style={td}><input value={draft.phone ?? ""} onChange={(e) => setDraft({...draft, phone: e.target.value})} placeholder="+15555550000" /></td>
            <td style={td}>
              {(["tier_1", "tier_2", "tier_3"] as const).map((t) => (
                <label key={t} style={{ marginRight: 8 }}>
                  <input type="checkbox" checked={draft.tiers.includes(t)} onChange={() => toggleTier(t)} /> {t}
                </label>
              ))}
            </td>
            <td style={td}><input type="checkbox" checked={draft.active} onChange={(e) => setDraft({...draft, active: e.target.checked})} /></td>
            <td style={td}><button disabled={!isCfo} onClick={add}>Add</button></td>
          </tr>
        </tbody>
      </table>
      <p>{status}</p>
    </section>
  );
}

const th: React.CSSProperties = {
  border: "1px solid #ccc", padding: "6px 10px", background: "#f6f6f6",
  fontSize: 12, textAlign: "left",
};
const td: React.CSSProperties = { border: "1px solid #eee", padding: "4px 8px", verticalAlign: "middle" };

function Pill({ t }: { t: string }) {
  const color = t === "tier_3" ? "#a31f1f" : t === "tier_2" ? "#a35d1f" : "#1d4fa3";
  return (
    <span style={{
      display: "inline-block", padding: "1px 6px", marginRight: 4,
      borderRadius: 3, background: color, color: "white", fontSize: 11,
    }}>{t}</span>
  );
}
