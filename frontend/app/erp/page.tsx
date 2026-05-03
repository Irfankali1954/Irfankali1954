"use client";

import { useState } from "react";
import { api } from "@/lib/api";

export default function ErpPage() {
  const [vendor, setVendor] = useState<"oracle" | "sap">("oracle");
  const [code, setCode] = useState("");
  const [result, setResult] = useState<string>("");

  async function run() {
    try {
      const r = await api.syncErp(vendor, code);
      setResult(JSON.stringify(r, null, 2));
    } catch (e) {
      setResult(String(e));
    }
  }

  return (
    <section>
      <h1>ERP / Gantt Bridge</h1>
      <p>Bi-directional sync against Oracle Fusion or SAP S/4HANA. Schedule import accepts P6 .XER and MS Project .MPP.</p>
      <div style={{ display: "flex", gap: 8 }}>
        <select value={vendor} onChange={(e) => setVendor(e.target.value as "oracle" | "sap")}>
          <option value="oracle">Oracle Fusion</option>
          <option value="sap">SAP S/4HANA</option>
        </select>
        <input placeholder="Project code" value={code} onChange={(e) => setCode(e.target.value)} />
        <button onClick={run} disabled={!code}>Sync</button>
      </div>
      {result && <pre>{result}</pre>}
    </section>
  );
}
