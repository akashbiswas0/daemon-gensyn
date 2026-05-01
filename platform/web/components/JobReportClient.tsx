"use client";

import { useEffect, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8010";
const isBaseSepolia = (network: string) => network === "base-sepolia" || network === "sepolia";
const explorerTxUrl = (network: string, txHash: string) =>
  isBaseSepolia(network) ? `https://sepolia.basescan.org/tx/${txHash}` : "#";
const networkLabel = (network: string) => (isBaseSepolia(network) ? "Base Sepolia" : network);
const keeperhubRunUrl = (runId: string) => `https://app.keeperhub.com/runs/${runId}`;

type Report = {
  job_id: string;
  status: string;
  summary?: string | null;
  planner_rationale?: string | null;
  planner_mode?: string;
  planner_verification_requested?: boolean;
  final_summary?: string | null;
  report_confidence?: number | null;
  report_scope?: string;
  report_labels?: string[];
  report_source?: string;
  report_summary_mode?: string;
  verifier_summary?: string | null;
  worker_diagnoses?: Array<{
    reservation_id: string;
    node_peer_id: string;
    node_region: string;
    diagnosis: string;
    confidence: number;
    suggested_next_step?: string | null;
    follow_up_summary?: string | null;
    source?: string;
  }>;
  results: Array<{
    receipt_id?: string;
    reservation_id: string;
    node_peer_id: string;
    node_region: string;
    task_type: string;
    success: boolean;
    measurement?: {
      status_code?: number | null;
      response_time_ms?: number | null;
      latency_ms?: number | null;
      packet_loss_percent?: number | null;
      provider?: string | null;
      cache_status?: string | null;
      dns_answers?: string[];
    };
    failure?: { message?: string } | null;
    settlement?: {
      status: string;
      amount: number;
      currency: string;
      worker_wallet: string;
      network: string;
      keeperhub_run_id?: string | null;
      tx_hash?: string | null;
      failure_reason?: string | null;
    } | null;
  }>;
  verification: Array<{
    status: string;
    notes?: string | null;
    settlement?: {
      status: string;
      amount: number;
      currency: string;
      worker_wallet: string;
      network: string;
      tx_hash?: string | null;
      failure_reason?: string | null;
    } | null;
  }>;
};

export function JobReportClient({ jobId }: { jobId: string }) {
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const response = await fetch(`${API_BASE}/reports/jobs/${jobId}`, {
          cache: "no-store",
        });
        if (!response.ok) {
          throw new Error(`Failed to load report (${response.status})`);
        }
        const payload = await response.json();
        if (alive) {
          setReport(payload);
          setError("");
        }
      } catch (err) {
        if (alive) {
          setError(err instanceof Error ? err.message : "Failed to load report");
        }
      }
    };
    load();
    const interval = window.setInterval(load, 2500);
    return () => {
      alive = false;
      window.clearInterval(interval);
    };
  }, [jobId]);

  if (error) {
    return <p className="muted">{error}</p>;
  }

  if (!report) {
    return <p className="muted">Loading report...</p>;
  }

  const compactSummary =
    report.final_summary ??
    report.summary ??
    "Waiting for signed receipts from remote peers.";

  return (
    <div className="stack">
      <div className="row">
        <span className="pill">{report.status.toLowerCase()}</span>
        <span className="pill">
          {report.report_source === "openai-assisted" || report.planner_mode === "openai-assisted"
            ? "NodeHub-assisted"
            : "Deterministic fallback"}
        </span>
        {report.planner_verification_requested ? <span className="pill">Verification requested</span> : null}
      </div>
      <div className="surface-card stack" style={{ gap: 8 }}>
        <div className="kicker">Summary</div>
        <p>{compactSummary}</p>
        <div className="row">
          {report.verifier_summary ? <span className="muted">{report.verifier_summary}</span> : null}
          {report.report_labels?.map((label) => (
            <span key={label} className="pill">
              {label}
            </span>
          ))}
        </div>
      </div>
      <table className="table">
        <thead>
          <tr>
            <th>Peer</th>
            <th>Region</th>
            <th>Result</th>
            <th>Metrics</th>
            <th>Payout</th>
          </tr>
        </thead>
        <tbody>
          {report.results.map((result, index) => {
            const verification = report.verification[index];
            const settlement = result.settlement;
            return (
              <tr key={`${result.reservation_id}-${result.node_peer_id}-${index}`}>
                <td>{result.node_peer_id.slice(0, 14)}...</td>
                <td>{result.node_region}</td>
                <td>
                  <strong>{result.success ? "Success" : "Failed"}</strong>
                  <div className="muted">
                    {verification ? verification.status.toLowerCase() : ""}
                  </div>
                </td>
                <td>
                  {typeof result.measurement?.status_code === "number" ? <div>Status {result.measurement.status_code}</div> : null}
                  {typeof result.measurement?.response_time_ms === "number" ? <div>{result.measurement.response_time_ms.toFixed(1)} ms</div> : null}
                  {typeof result.measurement?.latency_ms === "number" ? <div>{result.measurement.latency_ms.toFixed(1)} ms</div> : null}
                  {typeof result.measurement?.packet_loss_percent === "number" ? <div>Loss {result.measurement.packet_loss_percent.toFixed(1)}%</div> : null}
                  {result.measurement?.provider ? <div>CDN {result.measurement.provider}</div> : null}
                  {result.measurement?.cache_status ? <div>Cache {result.measurement.cache_status}</div> : null}
                  {result.measurement?.dns_answers?.length ? <div>{result.measurement.dns_answers.join(", ")}</div> : null}
                  {result.failure?.message ? <div>{result.failure.message}</div> : null}
                </td>
                <td>
                  {settlement ? (
                    <div className="stack" style={{ gap: 6 }}>
                      <span className="pill">{settlement.status}</span>
                      <div>
                        {settlement.amount} {settlement.currency}
                      </div>
                      <div className="muted">{networkLabel(settlement.network)}</div>
                      <div className="muted">
                        {settlement.worker_wallet.slice(0, 8)}...{settlement.worker_wallet.slice(-4)}
                      </div>
                      {settlement.tx_hash ? (
                        <div className="muted">
                          <a href={explorerTxUrl(settlement.network, settlement.tx_hash)} target="_blank" rel="noreferrer">
                            {settlement.tx_hash.slice(0, 12)}...
                          </a>
                        </div>
                      ) : settlement.keeperhub_run_id ? (
                        <div className="muted">
                          <a href={keeperhubRunUrl(settlement.keeperhub_run_id)} target="_blank" rel="noreferrer">
                            Run {settlement.keeperhub_run_id.slice(0, 12)}...
                          </a>
                        </div>
                      ) : settlement.failure_reason ? (
                        <div className="muted">{settlement.failure_reason}</div>
                      ) : (
                        <div className="muted">Awaiting tx hash</div>
                      )}
                    </div>
                  ) : (
                    <span className="muted">No payout</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {report.planner_rationale ? (
        <details className="surface-card">
          <summary>Planner</summary>
          <div className="stack" style={{ marginTop: 12 }}>
            <p>{report.planner_rationale}</p>
          </div>
        </details>
      ) : null}
      {report.worker_diagnoses?.length ? (
        <details className="surface-card">
          <summary>Diagnoses</summary>
          <div className="stack" style={{ marginTop: 12 }}>
            {report.worker_diagnoses.map((diagnosis) => (
              <div key={`${diagnosis.reservation_id}-${diagnosis.node_peer_id}`} className="stack" style={{ gap: 6 }}>
                <strong>
                  {diagnosis.node_region} · {diagnosis.node_peer_id.slice(0, 14)}...
                </strong>
                <p>{diagnosis.diagnosis}</p>
                <p className="muted">
                  {(diagnosis.confidence * 100).toFixed(0)}%
                  {diagnosis.suggested_next_step ? ` · ${diagnosis.suggested_next_step}` : ""}
                </p>
                {diagnosis.follow_up_summary ? <p className="muted">{diagnosis.follow_up_summary}</p> : null}
              </div>
            ))}
            <p className="muted">
              Scope: {report.report_scope ?? "inconclusive"}
              {typeof report.report_confidence === "number" ? ` · Confidence ${(report.report_confidence * 100).toFixed(0)}%` : ""}
            </p>
          </div>
        </details>
      ) : null}
    </div>
  );
}
