"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { RegionMultiSelect } from "./RegionMultiSelect";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8010";

type RegionOption = {
  region: string;
  countryCode: string;
};

export function CreateJobForm({ regionOptions = [] }: { regionOptions?: RegionOption[] }) {
  const router = useRouter();
  const [taskType, setTaskType] = useState<"browser_task" | "http_check">("browser_task");
  const [target, setTarget] = useState("https://example.com");
  const [method, setMethod] = useState("GET");
  const [timeoutSeconds, setTimeoutSeconds] = useState("10");
  const [browserTask, setBrowserTask] = useState("Find the page title and leave the browser on the evidence page.");
  const [regions, setRegions] = useState<string[]>([]);
  const [status, setStatus] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function submit() {
    setIsSubmitting(true);
    setStatus("");
    const body =
      taskType === "browser_task"
        ? {
            task_type: "browser_task",
            inputs: {
              url: target,
              task: browserTask,
              x402_sig: "demo-signature",
            },
            regions,
            verifier_count: 1,
          }
        : {
            task_type: "http_check",
            inputs: {
              url: target,
              method,
              timeout_seconds: Number(timeoutSeconds),
            },
            regions,
            verifier_count: 1,
          };
    const response = await fetch(`${API_BASE}/jobs/request`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });
    const payload = await response.json();
    if (response.ok) {
      setStatus(taskType === "browser_task" ? "Browser task dispatched. Opening report..." : "HTTP check dispatched. Opening report...");
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
          <select className="input" value={taskType} onChange={(event) => setTaskType(event.target.value as "browser_task" | "http_check")}>
            <option value="browser_task">browser_task</option>
            <option value="http_check">http_check</option>
          </select>
        </label>
        <div className="field">
          <span>Regions</span>
          <RegionMultiSelect value={regions} onChange={setRegions} placeholder="Select regions" options={regionOptions} />
        </div>
      </div>
      <label className="field">
        <span>{taskType === "browser_task" ? "Start URL" : "Target URL"}</span>
        <input
          className="input"
          value={target}
          onChange={(event) => setTarget(event.target.value)}
          placeholder="https://example.com"
        />
      </label>
      {taskType === "browser_task" ? (
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
      ) : (
        <div className="form-grid">
          <label className="field">
            <span>Method</span>
            <select className="input" value={method} onChange={(event) => setMethod(event.target.value)}>
              <option value="GET">GET</option>
              <option value="HEAD">HEAD</option>
            </select>
          </label>
          <label className="field">
            <span>Timeout</span>
            <input
              className="input"
              value={timeoutSeconds}
              onChange={(event) => setTimeoutSeconds(event.target.value)}
              placeholder="10"
            />
          </label>
        </div>
      )}
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <div className="muted">
          {taskType === "browser_task"
            ? "0G browser tasks run only on active operator nodes with the browser runtime enabled."
            : "HTTP checks run on the same live operator nodes for lightweight verification."}
        </div>
        <button type="button" className="button" onClick={submit} disabled={isSubmitting}>
          {isSubmitting ? "Dispatching..." : taskType === "browser_task" ? "Run Browser Task" : "Run HTTP Check"}
        </button>
      </div>
      {status ? <div className="pill">{status}</div> : null}
    </div>
  );
}
