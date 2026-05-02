"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { RegionMultiSelect } from "./RegionMultiSelect";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8010";

export function CreateJobForm() {
  const router = useRouter();
  const [taskType, setTaskType] = useState("http_check");
  const [target, setTarget] = useState("https://example.com");
  const [method, setMethod] = useState("GET");
  const [port, setPort] = useState("443");
  const [timeoutSeconds, setTimeoutSeconds] = useState("10");
  const [jsonBody, setJsonBody] = useState("");
  const [browserTask, setBrowserTask] = useState("Find the page title and leave the browser on the evidence page.");
  const [regions, setRegions] = useState<string[]>([]);
  const [jobId, setJobId] = useState("");
  const [status, setStatus] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const regionList = regions;

  const taskConfig = {
    http_check: {
      targetLabel: "Target URL",
      placeholder: "https://api.system.local/v1/health",
      hint: "Simple URL check"
    },
    dns_check: {
      targetLabel: "Target host",
      placeholder: "example.com",
      hint: "Regional DNS lookup"
    },
    latency_probe: {
      targetLabel: "Target host",
      placeholder: "example.com",
      hint: "TCP connect timing"
    },
    ping_check: {
      targetLabel: "Target host",
      placeholder: "1.1.1.1",
      hint: "ICMP ping from worker nodes"
    },
    api_call: {
      targetLabel: "API URL",
      placeholder: "https://httpbin.org/post",
      hint: "Real API request with method and optional JSON body"
    },
    cdn_check: {
      targetLabel: "Target URL",
      placeholder: "https://example.com",
      hint: "Live CDN header and cache inspection"
    },
    browser_task: {
      targetLabel: "Start URL",
      placeholder: "https://example.com",
      hint: "Run the node-nexus-agent browser workflow on the selected worker"
    }
  } as const;

  const config = taskConfig[taskType as keyof typeof taskConfig];
  const usesUrl = taskType === "http_check" || taskType === "api_call" || taskType === "cdn_check";
  const needsPort = taskType === "dns_check" || taskType === "latency_probe";
  const needsMethod = taskType === "http_check" || taskType === "api_call" || taskType === "cdn_check";
  const needsJsonBody = taskType === "api_call";
  const needsBrowserTask = taskType === "browser_task";
  const needsTimeout = taskType !== "browser_task";

  async function submit() {
    setIsSubmitting(true);
    let inputs: Record<string, unknown>;
    if (taskType === "http_check") {
      inputs = { url: target, method: method === "HEAD" ? "HEAD" : "GET", timeout_seconds: Number(timeoutSeconds) };
    } else if (taskType === "dns_check") {
      inputs = { hostname: target, port: Number(port) };
    } else if (taskType === "latency_probe") {
      inputs = { host: target, port: Number(port), timeout_seconds: Number(timeoutSeconds) };
    } else if (taskType === "ping_check") {
      inputs = { host: target, count: 3, timeout_seconds: Number(timeoutSeconds) };
    } else if (taskType === "api_call") {
      let parsedBody: unknown = undefined;
      if (jsonBody.trim()) {
        try {
          parsedBody = JSON.parse(jsonBody);
        } catch {
          setStatus("JSON body must be valid JSON.");
          setIsSubmitting(false);
          return;
        }
      }
      inputs = {
        url: target,
        method,
        timeout_seconds: Number(timeoutSeconds),
        ...(parsedBody !== undefined ? { json_body: parsedBody } : {})
      };
    } else if (taskType === "browser_task") {
      inputs = { url: target, task: browserTask, x402_sig: "demo-signature" };
    } else {
      inputs = { url: target, method: method === "GET" ? "GET" : "HEAD", timeout_seconds: Number(timeoutSeconds) };
    }
    const body = {
      task_type: taskType,
      inputs,
      regions: regionList,
      verifier_count: 1
    };
    const response = await fetch(`${API_BASE}/jobs/request`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(body)
    });
    const payload = await response.json();
    if (response.ok) {
      setJobId(payload.job_id);
      setStatus("Job executed. Opening the signed report...");
      router.push(`/jobs/${payload.job_id}`);
    } else {
      setStatus(payload.detail ?? JSON.stringify(payload));
    }
    setIsSubmitting(false);
  }

  return (
    <div className="stack">
      <div className="form-grid">
        <label className="field">
          <span>Job type</span>
          <select className="input" value={taskType} onChange={(event) => setTaskType(event.target.value)}>
            <option value="http_check">http_check</option>
            <option value="dns_check">dns_check</option>
            <option value="latency_probe">latency_probe</option>
            <option value="ping_check">ping_check</option>
            <option value="api_call">api_call</option>
            <option value="cdn_check">cdn_check</option>
            <option value="browser_task">browser_task</option>
          </select>
        </label>
        <div className="field">
          <span>Regions</span>
          <RegionMultiSelect value={regions} onChange={setRegions} placeholder="Select regions" />
        </div>
      </div>
      <label className="field">
        <span>{config.targetLabel}</span>
        <input
          className="input"
          value={target}
          onChange={(event) => setTarget(event.target.value)}
          placeholder={config.placeholder}
        />
      </label>
      <div className="form-grid">
        {needsMethod ? (
          <label className="field">
            <span>Method</span>
            <select className="input" value={method} onChange={(event) => setMethod(event.target.value)}>
              {taskType === "http_check" || taskType === "cdn_check" ? (
                <>
                  <option value="GET">GET</option>
                  <option value="HEAD">HEAD</option>
                </>
              ) : (
                <>
                  <option value="GET">GET</option>
                  <option value="POST">POST</option>
                  <option value="PUT">PUT</option>
                  <option value="PATCH">PATCH</option>
                  <option value="DELETE">DELETE</option>
                  <option value="HEAD">HEAD</option>
                  <option value="OPTIONS">OPTIONS</option>
                </>
              )}
            </select>
          </label>
        ) : null}
        {needsPort ? (
          <label className="field">
            <span>Port</span>
            <input className="input" value={port} onChange={(event) => setPort(event.target.value)} placeholder="443" />
          </label>
        ) : null}
        {needsTimeout ? (
          <label className="field">
            <span>Timeout</span>
            <input
              className="input"
              value={timeoutSeconds}
              onChange={(event) => setTimeoutSeconds(event.target.value)}
              placeholder={usesUrl ? "10" : "5"}
            />
          </label>
        ) : null}
      </div>
      {needsJsonBody ? (
        <label className="field">
          <span>JSON body</span>
          <textarea
            className="input"
            rows={5}
            value={jsonBody}
            onChange={(event) => setJsonBody(event.target.value)}
            placeholder='{"hello":"world"}'
          />
        </label>
      ) : null}
      {needsBrowserTask ? (
        <label className="field">
          <span>Browser task</span>
          <textarea
            className="input"
            rows={5}
            value={browserTask}
            onChange={(event) => setBrowserTask(event.target.value)}
            placeholder="Find the page title and leave the browser on the evidence page."
          />
        </label>
      ) : null}
      <span className="muted">{config.hint}</span>
      <button className="button" onClick={submit} disabled={isSubmitting}>
        {isSubmitting ? "Executing..." : "Run Job"}
      </button>
      {status ? <span className="muted">{status}</span> : null}
      {jobId ? <Link className="button secondary" href={`/jobs/${jobId}`}>Open Latest Report</Link> : null}
    </div>
  );
}
