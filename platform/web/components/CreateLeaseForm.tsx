"use client";

import { useState } from "react";

import { RegionMultiSelect } from "./RegionMultiSelect";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8010";

export function CreateLeaseForm() {
  const [capabilityName, setCapabilityName] = useState("http_check");
  const [regions, setRegions] = useState<string[]>([]);
  const [hours, setHours] = useState("1");
  const [status, setStatus] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function submit() {
    setIsSubmitting(true);
    const response = await fetch(`${API_BASE}/leases/request`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        capability_name: capabilityName,
        regions,
        duration_hours: Number(hours),
        verifier_count: 0
      })
    });
    const payload = await response.json();
    setStatus(response.ok ? `Lease negotiated ${payload.id}` : (payload.detail ?? JSON.stringify(payload)));
    setIsSubmitting(false);
  }

  return (
    <div className="stack">
      <label className="field">
        <span>Capability</span>
        <select className="input" value={capabilityName} onChange={(event) => setCapabilityName(event.target.value)}>
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
      <label className="field">
        <span>Hours</span>
        <input className="input" value={hours} onChange={(event) => setHours(event.target.value)} placeholder="1" />
      </label>
      <button className="button" onClick={submit} disabled={isSubmitting}>
        {isSubmitting ? "Creating..." : "Create Lease"}
      </button>
      {status ? <span className="muted">{status}</span> : null}
    </div>
  );
}
