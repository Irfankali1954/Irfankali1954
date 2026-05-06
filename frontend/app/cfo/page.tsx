"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type Summary = Awaited<ReturnType<typeof api.projectSummary>>;

export default function CfoPage() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.projectSummary(1).then(setSummary).catch((e) => setErr(String(e)));
  }, []);

  return (
    <section>
      <h1>CFO Command Center</h1>
      <p style={{ color: "#666" }}>
        Margin-masked at the API. Fields you cannot see are nulled by the
        Visibility Policy — not hidden in the UI.
      </p>
      {err && <pre style={{ color: "crimson" }}>{err}</pre>}
      {summary && (
        <table style={{ borderCollapse: "collapse" }}>
          <tbody>
            <Row label="Project" value={summary.code} />
            <Row label="Revenue" value={fmtMoney(summary.revenue)} />
            <Row label="Actual cost" value={fmtMoney(summary.actual_cost)} />
            <Row label="Margin" value={fmtMoney(summary.margin)} />
            <Row label="Margin %" value={summary.margin_percent?.toFixed(2) ?? "—"} />
            <Row label="Field idle cost" value={fmtMoney(summary.field_idle_cost)} />
          </tbody>
        </table>
      )}
    </section>
  );
}

function Row({ label, value }: { label: string; value: string | number | null }) {
  return (
    <tr>
      <td style={{ padding: 6, fontWeight: 600 }}>{label}</td>
      <td style={{ padding: 6 }}>{value ?? "— masked —"}</td>
    </tr>
  );
}

function fmtMoney(v: number | null | undefined) {
  return v == null ? null : `$${v.toLocaleString()}`;
}
