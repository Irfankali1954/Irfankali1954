"use client";

import { useState } from "react";
import { api } from "@/lib/api";

export default function SchedulePage() {
  const [transcript, setTranscript] = useState("");
  const [activityId, setActivityId] = useState("");
  const [out, setOut] = useState<string>("");

  async function submit() {
    try {
      const r = await api.submitDailyLog({
        project_id: 1,
        activity_id: activityId,
        raw_transcript: transcript,
      });
      setOut(JSON.stringify(r, null, 2));
    } catch (e) {
      setOut(String(e));
    }
  }

  return (
    <section>
      <h1>Autonomous Scheduler</h1>
      <p>Submit a 30-second daily log. Progress and blockers are extracted, the Gantt bar updates, and the Critical Path is recomputed.</p>
      <div style={{ display: "grid", gap: 8, maxWidth: 600 }}>
        <input placeholder="Activity ID (e.g. CIV-1040)" value={activityId} onChange={(e) => setActivityId(e.target.value)} />
        <textarea
          rows={4}
          placeholder='e.g. "Footing pour 60% complete. Blocker: rebar shortage."'
          value={transcript}
          onChange={(e) => setTranscript(e.target.value)}
        />
        <button onClick={submit} disabled={!activityId || !transcript}>Submit log</button>
      </div>
      {out && <pre>{out}</pre>}
    </section>
  );
}
