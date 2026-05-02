"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { RegionMultiSelect } from "./RegionMultiSelect";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8010";

export function CreateJobForm() {
  const router = useRouter();
  const [target, setTarget] = useState("https://example.com");
  const [browserTask, setBrowserTask] = useState("Find the page title and leave the browser on the evidence page.");
  const [regions, setRegions] = useState<string[]>([]);
  const [status, setStatus] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function submit() {
    setIsSubmitting(true);
    setStatus("");
    const response = await fetch(`${API_BASE}/jobs/request`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        task_type: "browser_task",
        inputs: {
          url: target,
          task: browserTask,
          x402_sig: "demo-signature",
        },
        regions,
        verifier_count: 1,
      }),
    });
    const payload = await response.json();
    if (response.ok) {
      setStatus("Browser task dispatched. Opening report...");
      router.push(`/jobs/${payload.job_id}`);
      return;
    }
    setStatus(payload.detail ?? JSON.stringify(payload));
    setIsSubmitting(false);
  }

  return (
    <div className="stack">
      <div className="form-grid">
        <label className="field">
          <span>Job type</span>
          <input className="input" value="browser_task" readOnly />
        </label>
        <div className="field">
          <span>Regions</span>
          <RegionMultiSelect value={regions} onChange={setRegions} placeholder="Select regions" />
        </div>
      </div>
      <label className="field">
        <span>Start URL</span>
        <input
          className="input"
          value={target}
          onChange={(event) => setTarget(event.target.value)}
          placeholder="https://example.com"
        />
      </label>
      <label className="field">
        <span>Browser task</span>
        <textarea
          className="input"
          rows={6}
          value={browserTask}
          onChange={(event) => setBrowserTask(event.target.value)}
          placeholder="Find the page title and leave the browser on the evidence page."
        />
      </label>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <div className="muted">0G browser tasks run only on active operator nodes with the browser agent enabled.</div>
        <button type="button" className="button" onClick={submit} disabled={isSubmitting}>
          {isSubmitting ? "Dispatching..." : "Run Browser Task"}
        </button>
      </div>
      {status ? <div className="pill">{status}</div> : null}
    </div>
  );
}
