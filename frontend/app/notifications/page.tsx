"use client";

/**
 * Alerts Feed.
 *
 * Tier-coded list of every notification the bus has produced. Each row
 * shows the channels it fanned out on (capture buffer in dev) so you can
 * verify Tier 3 actually reached the senior list.
 */

import { useEffect, useState } from "react";
import { api, type NotificationFeedItem } from "@/lib/api";

const PROJECT_ID = 1;

export default function NotificationsPage() {
  const [feed, setFeed] = useState<NotificationFeedItem[]>([]);
  const [status, setStatus] = useState("");

  async function refresh() {
    try { setFeed(await api.notifications(PROJECT_ID)); }
    catch (e) { setStatus(String(e)); }
  }
  useEffect(() => { refresh(); }, []);

  async function evalNow() {
    try {
      const n = await api.evaluateNotifications(PROJECT_ID);
      setStatus(n ? `evaluated → ${n.tier}` : "evaluated → suppressed (dedupe)");
      refresh();
    } catch (e) { setStatus(String(e)); }
  }

  return (
    <section>
      <h1>Alerts Feed</h1>
      <p>
        Auto-fired by every CPM recompute and every drafted Delay Claim.
        Three tiers — <Tag t="tier_1" /> dashboard only, <Tag t="tier_2" /> email
        the PD/CFO, <Tag t="tier_3" /> SMS the senior list.
      </p>
      <p>
        <button onClick={evalNow}>Evaluate now</button> <span>{status}</span>
      </p>
      {feed.length === 0 && <p style={{ color: "#666" }}>No notifications yet.</p>}
      {feed.map((n) => (
        <article key={n.id} style={{ borderLeft: `5px solid ${tierColor(n.tier)}`, padding: "10px 14px", margin: "10px 0", background: "#fafafa" }}>
          <header style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <Tag t={n.tier} />
            <strong>{n.subject}</strong>
            <span style={{ marginLeft: "auto", color: "#666", fontSize: 12 }}>
              {new Date(n.created_at).toLocaleString()}
            </span>
          </header>
          <pre style={{ whiteSpace: "pre-wrap", margin: "6px 0", fontFamily: "inherit" }}>{n.body}</pre>
          <details>
            <summary>Dispatched to ({n.dispatched_to.length})</summary>
            <ul>
              {n.dispatched_to.map((d, i) => (
                <li key={i}>
                  <code>{d.channel}</code> → {d.to} {d.ok ? "✓" : "✗"}
                </li>
              ))}
            </ul>
          </details>
        </article>
      ))}
    </section>
  );
}

function Tag({ t }: { t: string }) {
  return (
    <span style={{
      padding: "2px 8px", borderRadius: 3, fontSize: 11,
      background: tierColor(t), color: "white",
    }}>{t}</span>
  );
}

function tierColor(t: string): string {
  if (t === "tier_3") return "#a31f1f";
  if (t === "tier_2") return "#a35d1f";
  return "#1d4fa3";
}
