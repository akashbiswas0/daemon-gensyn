"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { clientApiBase } from "../lib/clientApiBase";

const API_BASE = clientApiBase();

type JobSummary = {
  id: string;
  task_type: string;
  status: string;
  regions: string[];
};

export function JobActivityPanel() {
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [error, setError] = useState<string>("");

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const response = await fetch(`${API_BASE}/jobs`, {
          cache: "no-store",
        });
        if (!response.ok) {
          throw new Error(`Failed to load jobs (${response.status})`);
        }
        const payload = await response.json();
        if (alive) {
          setJobs(payload);
          setError("");
        }
      } catch (err) {
        if (alive) {
          setError(err instanceof Error ? err.message : "Failed to load jobs");
        }
      }
    };

    load();
    const interval = window.setInterval(load, 2500);
    return () => {
      alive = false;
      window.clearInterval(interval);
    };
  }, []);

  return (
    <div className="surface-card stack activity-panel">
      <div className="stack-header">
        <div>
          <div className="kicker">Live Job Activity</div>
          <h3>Local signed reports from decentralized executions</h3>
        </div>
        {jobs.length > 0 && <span className="pill">{jobs.length}</span>}
      </div>
      {error ? (
        <p className="muted">{error}</p>
      ) : jobs.length === 0 ? (
        <p className="muted">No jobs yet. Run one above and the signed receipts will appear here automatically.</p>
      ) : (
        <div className="activity-feed">
          {jobs.map((job) => (
            <div key={job.id} className="inner-card">
              <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <strong>{job.task_type}</strong>
                  <div className="muted">{job.regions.join(", ") || "auto region"}</div>
                </div>
                <span className="pill">{job.status.toLowerCase()}</span>
              </div>
              <div className="row" style={{ marginTop: 12 }}>
                <Link className="button secondary" href={`/jobs/${job.id}`}>Open Report</Link>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
