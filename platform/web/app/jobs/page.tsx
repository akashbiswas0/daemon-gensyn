import { CreateJobForm } from "../../components/CreateJobForm";

export default function JobsPage() {
  return (
    <section className="surface-card stack">
      <div className="kicker">Execution Console</div>
      <h3>Launch a fresh browser-task job</h3>
      <p className="muted">Use this page when you want a focused surface for routing 0G-backed browser tasks to active operators.</p>
      <CreateJobForm />
    </section>
  );
}
