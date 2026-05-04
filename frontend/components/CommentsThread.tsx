"use client";

import { useEffect, useState } from "react";
import { api, type Comment, type Role } from "@/lib/api";

export function CommentsThread({ kind, id }: { kind: "idle_event" | "claim"; id: number }) {
  const [rows, setRows] = useState<Comment[]>([]);
  const [body, setBody] = useState("");
  const [status, setStatus] = useState("");
  const [role, setRole] = useState<Role | null>(null);

  useEffect(() => {
    setRole((typeof window !== "undefined"
      ? (window.localStorage.getItem("epc_role") as Role | null)
      : null));
    refresh();
  }, [kind, id]);

  async function refresh() {
    try { setRows(await api.listComments(kind, id)); }
    catch (e) { setStatus(String(e)); }
  }

  async function post() {
    if (!body.trim()) return;
    try {
      await api.postComment(kind, id, body.trim());
      setBody("");
      refresh();
    } catch (e) { setStatus(String(e)); }
  }

  const canWrite = role === "cfo" || role === "project_director";

  return (
    <section style={{ marginTop: 24, padding: 16, background: "#fffbe7", border: "1px solid #f1d978", borderRadius: 4 }}>
      <h3 style={{ marginTop: 0 }}>Management notes</h3>
      <p style={{ color: "#444", fontSize: 13 }}>
        CEO/CFO/PD only. Visible to the field team immediately — bypasses the silo.
      </p>
      <ul style={{ paddingLeft: 16 }}>
        {rows.map((c) => (
          <li key={c.id} style={{ marginBottom: 8 }}>
            <strong>{c.author_email}</strong>
            <span style={{ color: "#777", marginLeft: 6, fontSize: 12 }}>
              ({c.author_role}) · {new Date(c.created_at).toLocaleString()}
            </span>
            <div>{c.body}</div>
          </li>
        ))}
        {rows.length === 0 && <li style={{ color: "#888" }}>No notes yet.</li>}
      </ul>
      {canWrite && (
        <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
          <textarea
            rows={2} style={{ flex: 1 }}
            value={body} onChange={(e) => setBody(e.target.value)}
            placeholder="Counsel notified. File on Friday if not resolved."
          />
          <button onClick={post} disabled={!body.trim()}>Post</button>
        </div>
      )}
      {status && <p style={{ color: "crimson", fontSize: 12 }}>{status}</p>}
    </section>
  );
}
