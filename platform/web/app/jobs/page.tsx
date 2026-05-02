import { CreateJobForm } from "../../components/CreateJobForm";
import { getNodes } from "../../lib/api";

export const dynamic = "force-dynamic";

export default async function JobsPage() {
  const nodes = await getNodes().catch(() => []);
  const seen = new Map<string, string>();
  for (const node of nodes) {
    const region = String(node.region ?? "").toLowerCase();
    const countryCode = String(node.country_code ?? "").toUpperCase();
    if (!region || seen.has(region)) continue;
    seen.set(region, countryCode);
  }
  const regionOptions = Array.from(seen.entries())
    .map(([region, countryCode]) => ({ region, countryCode }))
    .sort((a, b) => a.region.localeCompare(b.region));

  return (
    <section className="surface-card stack">
      <div className="kicker">Execution Console</div>
      <h3>Launch a browser task or HTTP check</h3>
      <p className="muted">Route work to active operators. Browser tasks stay primary; HTTP checks remain available as a lightweight fallback.</p>
      <CreateJobForm regionOptions={regionOptions} />
    </section>
  );
}
