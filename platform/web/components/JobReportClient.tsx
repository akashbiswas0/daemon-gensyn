"use client";

import { useEffect, useState } from "react";

import { clientApiBase } from "../lib/clientApiBase";
const explorerTxUrl = (_network: string, txHash: string) => `https://chainscan-galileo.0g.ai/tx/${txHash}`;
const networkLabel = (network: string) =>
  network === "0g-galileo" || network === "0g" ? "0G Galileo" : network;
const screenshotName = (path: string) => path.split("/").pop() ?? path;

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
    raw?: {
      proof_hash?: string;
      proof_path?: string;
      orchestrator_url?: string;
      request?: {
        url?: string;
        task?: string;
      };
      response?: {
        ok?: boolean;
        error?: string;
        reportHash?: string;
        reportUri?: string;
        txHash?: string;
        reportPath?: string;
        artifactDir?: string;
        screenshots?: string[];
      };
    };
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
  request?: {
    inputs?: {
      url?: string;
      task?: string;
    };
  };
};

export function JobReportClient({ jobId }: { jobId: string }) {
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    const apiBase = clientApiBase();
    const load = async () => {
      const ctrl = new AbortController();
      // Hard timeout so a Local-Network-privacy stall (which doesn't reject
      // the fetch on macOS Sequoia) surfaces as a visible error instead of
      // leaving the user stuck on "Loading report...".
      const timer = window.setTimeout(() => ctrl.abort(), 8000);
      try {
        const response = await fetch(`${apiBase}/reports/jobs/${jobId}`, {
          cache: "no-store",
          signal: ctrl.signal,
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
          const message =
            err instanceof DOMException && err.name === "AbortError"
              ? `Daemon at ${apiBase} did not respond. Open this dashboard from the same hostname the daemon binds to (try http://127.0.0.1:3000).`
              : err instanceof Error
                ? err.message
                : "Failed to load report";
          setError(message);
        }
      } finally {
        window.clearTimeout(timer);
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
            : "0g-Reports"}
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
                  {result.raw?.proof_hash ? <div>Proof {result.raw.proof_hash}</div> : null}
                  {result.raw?.proof_path ? <div>{result.raw.proof_path}</div> : null}
                  {result.task_type === "browser_task" && result.raw?.response?.reportHash ? (
                    <div>Report {result.raw.response.reportHash.slice(0, 14)}...</div>
                  ) : null}
                  {result.task_type === "browser_task" && Array.isArray(result.raw?.response?.screenshots) ? (
                    <div>{result.raw!.response!.screenshots!.length} screenshot{result.raw!.response!.screenshots!.length === 1 ? "" : "s"}</div>
                  ) : null}
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
      {report.results
        .filter((r) => r.task_type === "browser_task")
        .map((result) => {
          const response = result.raw?.response;
          const request = result.raw?.request;
          const orchestrator = result.raw?.orchestrator_url;
          const screenshots = response?.screenshots ?? [];
          const startUrl = request?.url ?? report.request?.inputs?.url ?? null;
          const taskDescription = request?.task ?? report.request?.inputs?.task ?? null;
          return (
            <div key={`browser-${result.reservation_id}`} className="surface-card stack" style={{ gap: 12 }}>
              <div className="kicker">Browser task report</div>
              {startUrl ? (
                <div className="muted" style={{ wordBreak: "break-all" }}>
                  <span>Start URL · </span>
                  <a href={startUrl} target="_blank" rel="noreferrer">{startUrl}</a>
                </div>
              ) : null}
              {taskDescription ? <p style={{ margin: 0 }}>{taskDescription}</p> : null}
              {result.success ? (
                <>
                  {response?.reportHash ? (
                    <div className="row" style={{ alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                      <a
                        className="button button-small"
                        href={`https://indexer-storage-testnet-turbo.0g.ai/file?root=${response.reportHash}`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Download report PDF
                      </a>
                      <span className="muted">stored on 0G Storage testnet</span>
                    </div>
                  ) : null}
                  <div className="stack" style={{ gap: 6 }}>
                    {response?.reportHash ? (
                      <div className="stack" style={{ gap: 2 }}>
                        <span className="muted">Report hash</span>
                        <a
                          href={`https://indexer-storage-testnet-turbo.0g.ai/file?root=${response.reportHash}`}
                          target="_blank"
                          rel="noreferrer"
                          style={{ wordBreak: "break-all" }}
                        >
                          <code>{response.reportHash}</code>
                        </a>
                      </div>
                    ) : null}
                    {response?.txHash ? (
                      <div className="stack" style={{ gap: 2 }}>
                        <span className="muted">Storage tx</span>
                        <a
                          href={`https://chainscan-galileo.0g.ai/tx/${response.txHash}`}
                          target="_blank"
                          rel="noreferrer"
                          style={{ wordBreak: "break-all" }}
                        >
                          <code>{response.txHash}</code>
                        </a>
                      </div>
                    ) : null}
                    {screenshots.length ? (
                      <div className="meta-row" style={{ gap: 8 }}>
                        <span className="muted">Evidence</span>
                        <span>{screenshots.length} screenshot{screenshots.length === 1 ? "" : "s"}</span>
                      </div>
                    ) : null}
                  </div>
                  {screenshots.length ? (
                    <details>
                      <summary className="muted" style={{ cursor: "pointer" }}>
                        Step-by-step evidence ({screenshots.length})
                      </summary>
                      <ul style={{ marginTop: 8, paddingLeft: 20, columns: screenshots.length > 4 ? 2 : 1 }}>
                        {screenshots.map((path) => (
                          <li key={path}>
                            <code className="muted">{screenshotName(path)}</code>
                          </li>
                        ))}
                      </ul>
                    </details>
                  ) : null}
                  {response?.reportPath ? (
                    <div className="muted" style={{ fontSize: "0.85em" }}>
                      Operator artifact: <code>{response.reportPath}</code>
                    </div>
                  ) : null}
                </>
              ) : (
                <div>
                  <strong>Orchestrator error:&nbsp;</strong>
                  <span>{response?.error ?? result.failure?.message ?? "no message returned"}</span>
                  {orchestrator ? (
                    <div className="muted" style={{ marginTop: 4 }}>via {orchestrator}</div>
                  ) : null}
                </div>
              )}
            </div>
          );
        })}
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
