"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type Score = Awaited<ReturnType<typeof api.wrapScore>>;

export default function RiskPage() {
  const [score, setScore] = useState<Score | null>(null);
  const [misses, setMisses] = useState<unknown[]>([]);
  const [err, setErr] = useState<string>("");

  useEffect(() => {
    Promise.all([api.wrapScore(1), api.rfcMisses(1)])
      .then(([s, m]) => { setScore(s); setMisses(m); })
      .catch((e) => setErr(String(e)));
  }, []);

  return (
    <section>
      <h1>Master-Wrap Risk Engine</h1>
      {err && <pre style={{ color: "crimson" }}>{err}</pre>}
      {score && (
        <>
          <h2 style={{ fontSize: 48, margin: "8px 0" }}>{score.score.toFixed(1)}</h2>
          <p>Probability of hitting the Commercial Operation Date.</p>
          <ul>
            <li>Schedule factor: {score.schedule_factor.toFixed(2)}</li>
            <li>RFC factor: {score.rfc_factor.toFixed(2)}</li>
            <li>Permit factor: {score.permit_factor.toFixed(2)}</li>
            <li>Long-lead factor: {score.long_lead_factor.toFixed(2)}</li>
            <li>Field idle factor: {score.field_idle_factor.toFixed(2)}</li>
          </ul>
        </>
      )}
      <h3>RFC misses</h3>
      <pre>{JSON.stringify(misses, null, 2)}</pre>
    </section>
  );
}
