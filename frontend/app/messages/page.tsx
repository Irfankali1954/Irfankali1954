"use client";

/**
 * Inter-Company Messaging — context-aware composer.
 *
 * The composer requires the sender to pick a *context* (Gantt activity, RFC
 * drawing, or permit) before the body can be sent. The message renders
 * inline next to the artifact reference so the recipient sees what is being
 * referenced.
 */

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

const PROJECT_ID = 1;

type Threads = Awaited<ReturnType<typeof api.listThreads>>;
type CtxType = "activity" | "rfc" | "permit";

export default function MessagesPage() {
  const [threads, setThreads] = useState<Threads>([]);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [ctxType, setCtxType] = useState<CtxType>("rfc");
  const [activityId, setActivityId] = useState("CIV-1040");
  const [rfcId, setRfcId] = useState<number>(1);
  const [permitId, setPermitId] = useState<number>(1);
  const [status, setStatus] = useState("");

  async function load() {
    try {
      setThreads(await api.listThreads(PROJECT_ID));
    } catch (e) { setStatus(String(e)); }
  }

  useEffect(() => { load(); }, []);

  async function send() {
    setStatus("sending…");
    try {
      await api.sendMessage({
        project_id: PROJECT_ID,
        subject: subject || "Untitled",
        body,
        context: ctxType === "activity"
          ? { type: "activity", activity_id: activityId }
          : ctxType === "rfc"
            ? { type: "rfc", rfc_drawing_id: rfcId }
            : { type: "permit", permit_id: permitId },
      });
      setBody("");
      setStatus("sent.");
      load();
    } catch (e) { setStatus(`error: ${e}`); }
  }

  return (
    <section>
      <h1>Inter-Company Messaging</h1>
      <p>
        Tag a Gantt activity, RFC drawing, or permit so the conversation stays
        anchored to the artifact. Mentions like <code>@civil_lead</code> are
        extracted automatically.
      </p>

      <div style={{ display: "grid", gap: 8, maxWidth: 720, marginBottom: 24 }}>
        <input placeholder="Subject (new thread)" value={subject} onChange={(e) => setSubject(e.target.value)} />
        <div style={{ display: "flex", gap: 8 }}>
          <select value={ctxType} onChange={(e) => setCtxType(e.target.value as CtxType)}>
            <option value="activity">Gantt activity</option>
            <option value="rfc">RFC drawing</option>
            <option value="permit">Permit</option>
          </select>
          {ctxType === "activity" && (
            <input value={activityId} onChange={(e) => setActivityId(e.target.value)} placeholder="Activity ID" />
          )}
          {ctxType === "rfc" && (
            <input type="number" value={rfcId} onChange={(e) => setRfcId(Number(e.target.value))} placeholder="RFC drawing id" />
          )}
          {ctxType === "permit" && (
            <input type="number" value={permitId} onChange={(e) => setPermitId(Number(e.target.value))} placeholder="Permit id" />
          )}
        </div>
        <textarea
          rows={3}
          placeholder="@civil_lead foundation crew arrives in 48h — what is the status of X-102?"
          value={body}
          onChange={(e) => setBody(e.target.value)}
        />
        <div style={{ display: "flex", gap: 8 }}>
          <button onClick={send} disabled={!body}>Send</button>
          <span>{status}</span>
        </div>
      </div>

      <h3>Threads</h3>
      {threads.length === 0 && <p style={{ color: "#666" }}>No threads yet.</p>}
      {threads.map((t) => (
        <article key={t.id} style={{ border: "1px solid #ddd", padding: 12, marginBottom: 12, borderRadius: 4 }}>
          <strong>{t.subject}</strong>
          <span style={{ color: "#666", marginLeft: 8 }}>#{t.id}</span>
          <ul style={{ marginTop: 8 }}>
            {t.messages.map((m) => (
              <li key={m.id}>
                <code>{m.sender_email}</code>
                {m.activity_id && <Tag>activity={m.activity_id}</Tag>}
                {m.rfc_drawing_id != null && <Tag>rfc#{m.rfc_drawing_id}</Tag>}
                {m.permit_id != null && <Tag>permit#{m.permit_id}</Tag>}
                <div>{m.body}</div>
              </li>
            ))}
          </ul>
        </article>
      ))}
    </section>
  );
}

function Tag({ children }: { children: React.ReactNode }) {
  return (
    <span style={{ background: "#eef", color: "#225", padding: "1px 6px", marginLeft: 6, borderRadius: 3, fontSize: 11 }}>
      {children}
    </span>
  );
}
