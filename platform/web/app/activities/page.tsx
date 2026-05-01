import { JobActivityPanel } from "../../components/JobActivityPanel";
import { LeaseActivityPanel } from "../../components/LeaseActivityPanel";

export default function ActivitiesPage() {
  return (
    <div className="page-stack">
      <section className="overview-grid">
        <article className="surface-card">
          <div className="kicker">Execution Feed</div>
          <h3>Live job receipts</h3>
          <p className="muted">Fresh signed job results.</p>
        </article>
        <article className="surface-card">
          <div className="kicker">Lease Feed</div>
          <h3>Capacity movement</h3>
          <p className="muted">Accepted leases and expiry.</p>
        </article>
        <article className="surface-card">
          <div className="kicker">Focus</div>
          <h3>One activity feed</h3>
          <p className="muted">Jobs and leases only.</p>
        </article>
      </section>
      <section className="activity-grid">
        <JobActivityPanel />
        <LeaseActivityPanel />
      </section>
    </div>
  );
}
