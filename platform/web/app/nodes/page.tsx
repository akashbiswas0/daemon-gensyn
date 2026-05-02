import { CopyableId } from "../../components/CopyableId";
import { getNodes } from "../../lib/api";

export const dynamic = "force-dynamic";

export default async function NodesPage() {
  const nodes = await getNodes().catch(() => []);
  const activeNodes = nodes.filter((node: any) => node.active);

  return (
    <div className="page-stack">
      <section className="overview-grid">
        <article className="surface-card">
          <div className="kicker">Network Reach</div>
          <h3>{activeNodes.length} active peers</h3>
          <p className="muted">Signed worker ads in local state.</p>
        </article>
        <article className="surface-card">
          <div className="kicker">Capabilities</div>
          <h3>
            {activeNodes.reduce(
              (total: number, node: any) => total + (node.capabilities?.length ?? 0),
              0,
            )}{" "}
            advertised tools
          </h3>
          <p className="muted">Active browser-task tools per worker.</p>
        </article>
        <article className="surface-card">
          <div className="kicker">Visibility</div>
          <h3>{activeNodes.length} shown</h3>
          <p className="muted">Inactive peers are hidden from the dashboard.</p>
        </article>
      </section>

      <section className="surface-card">
        <div className="stack-header">
          <div>
            <div className="kicker">Discovered Peers</div>
            <h3>Known worker advertisements</h3>
          </div>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>Label</th>
              <th>Region</th>
              <th>Peer ID</th>
              <th>Capabilities</th>
              <th>Concurrency</th>
            </tr>
          </thead>
          <tbody>
            {activeNodes.map((node: any) => (
              <tr key={node.id}>
                <td>
                  <div className="node-label">
                    <span
                      className="node-status-dot is-active"
                      title="Active"
                      aria-label="Active"
                    />
                    <strong>{node.label}</strong>
                  </div>
                  <div className="muted">Active</div>
                </td>
                <td>
                  {node.region} / {node.country_code}
                </td>
                <td><CopyableId value={node.peer_id} ariaLabel="Copy peer ID" /></td>
                <td>{node.capabilities.map((cap: any) => cap.name).join(", ")}</td>
                <td>{node.max_concurrency}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
