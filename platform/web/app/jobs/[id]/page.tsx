import Link from "next/link";
import { JobReportClient } from "../../../components/JobReportClient";

export const dynamic = "force-dynamic";

export default async function JobDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <section className="surface-card">
      <div className="kicker"></div>
      <h2>{id}</h2>
      <p className="muted">Polling your local daemon for signed receipts.</p>
      <JobReportClient jobId={id} />
      <div className="row" style={{ marginTop: 16 }}>
        <Link className="button secondary" href="/jobs">Back to jobs</Link>
      </div>
    </section>
  );
}
