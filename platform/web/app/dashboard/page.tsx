import Link from "next/link";

import { CopyableId } from "../../components/CopyableId";
import { RegionHeatmap } from "../../components/RegionHeatmap";
import { getIdentity, getJobs, getNodes } from "../../lib/api";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  const [identity, nodes, jobs] = await Promise.all([
    getIdentity().catch(() => null),
    getNodes().catch(() => []),
    getJobs().catch(() => []),
  ]);
  const liveNodes = nodes;
  const completedJobs = jobs.filter((job: any) => job.status === "completed");

  const regionSet = new Set<string>();
  for (const node of liveNodes) regionSet.add((node.region || "unknown").toLowerCase());

  const recentJobs = jobs.slice(0, 4);
  return (
    <div className="dashboard-stack">
      <section className="dash-kpis">
        <div className="kpi">
          <span className="kpi-label">Active operators</span>
          <strong className="kpi-num">{liveNodes.length}</strong>
          <span className="kpi-sub">live now</span>
        </div>
        <div className="kpi">
          <span className="kpi-label">Signed jobs</span>
          <strong className="kpi-num">{jobs.length}</strong>
          <span className="kpi-sub">{completedJobs.length} completed</span>
        </div>
        <div className="kpi">
          <span className="kpi-label">Execution mode</span>
          <strong className="kpi-num">Browser + HTTP</strong>
          <span className="kpi-sub">0G primary, HTTP fallback</span>
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

        <RegionHeatmap initialNodes={liveNodes} className="dashboard-heatmap" />
      </section>

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
          <div className="kicker">Focus</div>
          <h3>Browser-first operator mesh</h3>
          <p className="muted">
            Only active operators are shown and targeted. Browser tasks stay primary, with HTTP checks retained as the
            lightweight fallback capability.
          </p>
          <div className="stack" style={{ gap: 8 }}>
            <div className="meta-row">
              <span className="muted">Primary capability</span>
              <strong>browser_task</strong>
            </div>
            <div className="meta-row">
              <span className="muted">Secondary capability</span>
              <strong>http_check</strong>
            </div>
            <div className="meta-row">
              <span className="muted">Worker onboarding</span>
              <strong>Wallet-bound local operators</strong>
            </div>
          </div>
        </article>
      </section>
    </div>
  );
}
