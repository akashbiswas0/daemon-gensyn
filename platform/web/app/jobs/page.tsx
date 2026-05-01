import { CreateJobForm } from "../../components/CreateJobForm";

export default function JobsPage() {
  return (
    <section className="surface-card stack">
      <div className="kicker">Execution Console</div>
      <h3>Launch a fresh decentralized WebOps job</h3>
      <p className="muted">Use this page when you want a focused job-launching surface without leaving the operator console context.</p>
      <CreateJobForm />
    </section>
  );
}
