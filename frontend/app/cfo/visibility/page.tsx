"use client";

/**
 * CFO Visibility Policy Editor.
 *
 * Matrix of FinancialField × Role. Each cell is a checkbox the CFO toggles.
 * Persisted via PUT /cfo/visibility-policy. The Admin cannot reach this page
 * because the route only renders for the cfo role; the API also denies any
 * non-CFO PUT.
 */

import { useEffect, useMemo, useState } from "react";
import { api, type Role } from "@/lib/api";

type Snapshot = Awaited<ReturnType<typeof api.readVisibility>>;

export default function VisibilityPage() {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [allowed, setAllowed] = useState<Record<Role, Set<string>>>({} as Record<Role, Set<string>>);
  const [status, setStatus] = useState<string>("");

  useEffect(() => {
    api.readVisibility()
      .then((s) => {
        setSnap(s);
        const next: Record<Role, Set<string>> = {} as Record<Role, Set<string>>;
        for (const r of s.roles) next[r] = new Set(s.policy[r] ?? []);
        setAllowed(next);
      })
      .catch((e) => setStatus(`load failed: ${e}`));
  }, []);

  function toggle(role: Role, key: string) {
    setAllowed((prev) => {
      const set = new Set(prev[role]);
      set.has(key) ? set.delete(key) : set.add(key);
      return { ...prev, [role]: set };
    });
  }

  async function save() {
    if (!snap) return;
    setStatus("saving…");
    try {
      await api.writeVisibility(
        snap.roles.map((r) => ({ role: r, allowed_fields: [...(allowed[r] ?? [])] })),
      );
      setStatus("saved.");
    } catch (e) {
      setStatus(`error: ${e}`);
    }
  }

  const isCfo = useMemo(() => snap?.viewer_role === "cfo", [snap]);

  if (!snap) return <p>Loading visibility policy…</p>;

  return (
    <section>
      <h1>Visibility Policy (CFO only)</h1>
      <p>
        Toggle which financial data classes each role is allowed to see. Anything
        unchecked is masked at the API boundary — the value never reaches the
        client. Admin is read-only here because Admin manages technology, not
        financial visibility.
      </p>
      {!isCfo && (
        <p style={{ color: "#a00" }}>
          You are signed in as <code>{snap.viewer_role}</code>. The save button
          is disabled — only the CFO can mutate the policy.
        </p>
      )}
      <div style={{ overflowX: "auto" }}>
        <table style={{ borderCollapse: "collapse", marginTop: 12 }}>
          <thead>
            <tr>
              <th style={th}>Field</th>
              {snap.roles.map((r) => (
                <th key={r} style={th}>{r}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {snap.fields.map((f) => (
              <tr key={f.key}>
                <td style={{ ...td, textAlign: "left", fontWeight: 600 }}>{f.label}</td>
                {snap.roles.map((r) => {
                  const checked = allowed[r]?.has(f.key) ?? false;
                  return (
                    <td key={r} style={td}>
                      <input
                        type="checkbox"
                        disabled={!isCfo}
                        checked={checked}
                        onChange={() => toggle(r, f.key)}
                      />
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ marginTop: 16, display: "flex", gap: 12, alignItems: "center" }}>
        <button onClick={save} disabled={!isCfo}>Save policy</button>
        <span>{status}</span>
      </div>
    </section>
  );
}

const th: React.CSSProperties = {
  border: "1px solid #ccc", padding: "6px 10px", background: "#f6f6f6",
  fontSize: 12, textAlign: "center", whiteSpace: "nowrap",
};
const td: React.CSSProperties = {
  border: "1px solid #eee", padding: "4px 10px", textAlign: "center",
};
