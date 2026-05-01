import { CreateLeaseForm } from "../../components/CreateLeaseForm";

export default function LeasesPage() {
  return (
    <section className="surface-card stack">
      <div className="kicker">Lease Rentals</div>
      <h3>Negotiate signed regional capacity windows</h3>
      <p className="muted">
        Request leases directly from the peers you discover over AXL, then reuse accepted lease-backed capacity for future jobs.
      </p>
      <CreateLeaseForm />
    </section>
  );
}
