import Link from "next/link";

import { CopyableId } from "../../components/CopyableId";
import { DaemonHealthPanel } from "../../components/DaemonHealthPanel";
import { LeaseGantt } from "../../components/LeaseGantt";
import { RegionHeatmap } from "../../components/RegionHeatmap";
import { getIdentity, getJobs, getLeases, getNodes } from "../../lib/api";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  const [identity, nodes, jobs, leases] = await Promise.all([
    getIdentity().catch(() => null),
    getNodes().catch(() => []),
    getJobs().catch(() => []),
    getLeases().catch(() => []),
  ]);
  const activeNodes = nodes.filter((node: any) => node.active);
  const activeLeases = leases.filter((lease: any) => lease.status === "active");
  const completedJobs = jobs.filter((job: any) => job.status === "completed");

  const regionSet = new Set<string>();
  for (const node of nodes) regionSet.add((node.region || "unknown").toLowerCase());

  const recentJobs = jobs.slice(0, 4);
  const recentLeases = leases.slice(0, 4);

  return (
    <div className="dashboard-stack">
      <section className="dash-kpis">
        <div className="kpi">
          <span className="kpi-label">Active nodes</span>
          <strong className="kpi-num">{activeNodes.length}</strong>
          <span className="kpi-sub">of {nodes.length} known</span>
        </div>
        <div className="kpi">
          <span className="kpi-label">Signed jobs</span>
          <strong className="kpi-num">{jobs.length}</strong>
          <span className="kpi-sub">{completedJobs.length} completed</span>
        </div>
        <div className="kpi">
          <span className="kpi-label">Active leases</span>
          <strong className="kpi-num">{activeLeases.length}</strong>
          <span className="kpi-sub">of {leases.length} total</span>
        </div>
        <div className="kpi">
          <span className="kpi-label">Regions</span>
          <strong className="kpi-num">{regionSet.size || 0}</strong>
          <span className="kpi-sub">discovered</span>
        </div>
      </section>

      <section className="overview-grid">
        <article className="surface-card">
          <div className="kicker">Identity</div>
          <h3>{identity?.label ?? "Local Operator"}</h3>
          <div className="identity-box">
            {identity?.wallet_address ? (
              <CopyableId
                value={identity.wallet_address}
                display={`${identity.wallet_address.slice(0, 10)}...${identity.wallet_address.slice(-6)}`}
                ariaLabel="Copy wallet address"
              />
            ) : (
              "Local daemon unavailable"
            )}
          </div>
          <div className="meta-row">
            <span className="muted">Region</span>
            <strong>{identity?.region ? identity.region.toUpperCase() : "—"}</strong>
          </div>
          <div className="meta-row">
            <span className="muted">Verification</span>
            <strong>Signed</strong>
          </div>
        </article>

        <RegionHeatmap />

        <DaemonHealthPanel />
      </section>

      <LeaseGantt />

      <section className="activity-grid">
        <article className="surface-card">
          <div className="stack-header">
            <div>
              <div className="kicker">Recent</div>
              <h3>Jobs</h3>
            </div>
            <Link className="button button-ghost button-small" href="/jobs">
              All jobs →
            </Link>
          </div>
          {recentJobs.length === 0 ? (
            <p className="muted">No jobs yet. Submit one from the Jobs page.</p>
          ) : (
            <ul className="activity-list">
              {recentJobs.map((job: any) => (
                <li key={job.id} className="activity-row">
                  <div>
                    <strong>{job.task_type}</strong>
                    <div className="muted">{job.regions?.join(", ") || "auto region"}</div>
                  </div>
                  <div className="activity-row-end">
                    <span className={`status-pill status-${job.status?.toLowerCase()}`}>
                      {job.status?.toLowerCase()}
                    </span>
                    <Link href={`/jobs/${job.id}`} className="row-link">Open →</Link>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </article>

        <article className="surface-card">
          <div className="stack-header">
            <div>
              <div className="kicker">Recent</div>
              <h3>Leases</h3>
            </div>
            <Link className="button button-ghost button-small" href="/leases">
              All leases →
            </Link>
          </div>
          {recentLeases.length === 0 ? (
            <p className="muted">No leases yet. Reserve capacity from the Leases page.</p>
          ) : (
            <ul className="activity-list">
              {recentLeases.map((lease: any) => (
                <li key={lease.id} className="activity-row">
                  <div>
                    <strong>{lease.filters?.regions?.join(", ") || "any region"}</strong>
                    <div className="muted">
                      {lease.accepted_peer_ids?.length ?? 0} peers
                      {lease.lease_window?.ends_at
                        ? ` · ends ${new Date(lease.lease_window.ends_at).toLocaleString()}`
                        : ""}
                    </div>
                  </div>
                  <span className={`status-pill status-${lease.status?.toLowerCase()}`}>
                    {lease.status?.toLowerCase()}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </article>
      </section>
    </div>
  );
}
