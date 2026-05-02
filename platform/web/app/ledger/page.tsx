import { CopyableId } from "../../components/CopyableId";
import { getAttestations, getNodes, getSettlements } from "../../lib/api";

export const dynamic = "force-dynamic";
const isBaseSepolia = (network: string) => network === "base-sepolia" || network === "sepolia";
const explorerTxUrl = (network: string, txHash: string) =>
  isBaseSepolia(network) ? `https://sepolia.basescan.org/tx/${txHash}` : "#";
const networkLabel = (network: string) => (isBaseSepolia(network) ? "Base Sepolia" : network);

export default async function LedgerPage() {
  const attestations = await getAttestations().catch(() => []);
  const nodes = await getNodes().catch(() => []);
  const settlements = await getSettlements().catch(() => []);
  const confirmedSettlements = settlements.filter((item: any) => item.status === "confirmed");
  const activeSettlementNetwork = confirmedSettlements[0]?.network ?? settlements[0]?.network ?? "base-sepolia";
  return (
    <div className="page-stack">
      <section className="overview-grid">
        <article className="surface-card">
          <div className="kicker">Trust Model</div>
          <h3>{attestations.length} signed attestations</h3>
          <p className="muted">Local trust signals only.</p>
        </article>
        <article className="surface-card">
          <div className="kicker">Payments</div>
          <h3>{settlements.length} settlement records</h3>
          <p className="muted">{confirmedSettlements.length} confirmed on {networkLabel(activeSettlementNetwork)}.</p>
        </article>
        <article className="surface-card">
          <div className="kicker">View</div>
          <h3>Payout visibility</h3>
          <p className="muted">Status only. No manual controls.</p>
        </article>
      </section>
      <section className="surface-card stack">
        <div className="kicker">Worker Trust</div>
        <h3>Known peer signals</h3>
        {nodes.length === 0 ? (
          <p className="muted">No discovered workers yet.</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Peer</th>
                <th>Region</th>
                <th>Signals</th>
                <th>Disputes</th>
              </tr>
            </thead>
            <tbody>
              {nodes.map((node: any) => (
                <tr key={node.peer_id}>
                  <td>{node.label}</td>
                  <td>{node.region}</td>
                  <td>{node.verifier_backed_successes ?? 0}</td>
                  <td>{node.mismatch_count ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      <section className="surface-card stack">
        <div className="stack-header">
          <div>
            <div className="kicker">Settlement Rail</div>
            <h3>Settlement records</h3>
          </div>
          {settlements.length > 0 && <span className="pill">{settlements.length}</span>}
        </div>
        {settlements.length === 0 ? (
          <p className="muted">No completed payout records yet.</p>
        ) : (
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>Status</th>
                  <th>Peer</th>
                  <th>Amount</th>
                  <th>Wallet</th>
                  <th>Network</th>
                  <th>Tx</th>
                </tr>
              </thead>
              <tbody>
                {settlements.map((settlement: any) => (
                  <tr key={settlement.settlement_id}>
                    <td><span className="pill">{settlement.status}</span></td>
                    <td>
                      <CopyableId value={settlement.worker_peer_id} ariaLabel="Copy worker peer ID" />
                    </td>
                    <td>{settlement.amount} {settlement.currency}</td>
                    <td>
                      <CopyableId
                        value={settlement.worker_wallet}
                        display={`${settlement.worker_wallet.slice(0, 8)}...${settlement.worker_wallet.slice(-4)}`}
                        ariaLabel="Copy worker wallet"
                      />
                    </td>
                    <td>{networkLabel(settlement.network)}</td>
                    <td>
                      {settlement.tx_hash ? (
                        <a href={explorerTxUrl(settlement.network, settlement.tx_hash)} target="_blank" rel="noreferrer">
                          {settlement.tx_hash.slice(0, 12)}...
                        </a>
                      ) : (
                        settlement.failure_reason ?? "Awaiting tx hash"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <section className="surface-card stack">
        <div className="stack-header">
          <div>
            <div className="kicker">Local Trust Ledger</div>
            <h3>Signed attestations</h3>
          </div>
          {attestations.length > 0 && <span className="pill">{attestations.length}</span>}
        </div>
        {attestations.length === 0 ? (
          <p className="muted">Run a job to generate trust records.</p>
        ) : (
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>Verdict</th>
                  <th>Subject Peer</th>
                  <th>Issuer</th>
                  <th>Notes</th>
                </tr>
              </thead>
              <tbody>
                {attestations.map((attestation: any) => (
                  <tr key={attestation.attestation_id}>
                    <td><span className="pill">{attestation.verdict}</span></td>
                    <td>
                      <CopyableId
                        value={attestation.subject_peer_id}
                        ariaLabel="Copy subject peer ID"
                      />
                    </td>
                    <td>
                      <CopyableId
                        value={attestation.issuer_wallet}
                        display={`${attestation.issuer_wallet.slice(0, 8)}...${attestation.issuer_wallet.slice(-4)}`}
                        ariaLabel="Copy issuer wallet"
                      />
                    </td>
                    <td>{attestation.notes ?? "No notes"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
