import Link from "next/link";

import { getJobs } from "../../lib/api";

export const dynamic = "force-dynamic";

function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const then = Date.parse(iso);
  if (!Number.isFinite(then)) return "";
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export default async function ActivitiesPage() {
  const jobs = await getJobs().catch(() => []);

  return (
    <section className="surface-card stack">
      <div className="stack-header">
        <div>
          <div className="kicker">Recent</div>
          <h3>Jobs</h3>
        </div>
        <Link className="button button-ghost button-small" href="/jobs">
          New job →
        </Link>
      </div>
      {jobs.length === 0 ? (
        <p className="muted">No jobs yet. Submit one from the Jobs page.</p>
      ) : (
        <ul className="activity-list">
          {jobs.map((job: any) => (
            <li key={job.id} className="activity-row">
              <div>
                <strong>{job.task_type}</strong>
                <div className="muted">
                  {job.regions?.join(", ") || "auto region"}
                  {job.submitted_at ? ` · ${relativeTime(job.submitted_at)}` : ""}
                </div>
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
    </section>
  );
}
