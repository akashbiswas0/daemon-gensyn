"use client";

import { useEffect, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8010";

type LeaseSummary = {
  id: string;
  status: string;
  reservation_ids: string[];
  accepted_peer_ids: string[];
  lease_window: { starts_at: string; ends_at: string };
  filters: { regions?: string[] };
};

export function LeaseActivityPanel() {
  const [leases, setLeases] = useState<LeaseSummary[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const leaseRes = await fetch(`${API_BASE}/leases`, { cache: "no-store" });
        if (!leaseRes.ok) {
          throw new Error(`Failed to load leases (${leaseRes.status})`);
        }
        const leasePayload = await leaseRes.json();
        if (alive) {
          setLeases(leasePayload);
          setError("");
        }
      } catch (err) {
        if (alive) {
          setError(err instanceof Error ? err.message : "Failed to load lease activity");
        }
      }
    };

    load();
    const interval = window.setInterval(load, 4000);
    return () => {
      alive = false;
      window.clearInterval(interval);
    };
  }, []);

  return (
    <div className="surface-card stack">
      <div>
        <div className="kicker">Lease Activity</div>
        <h3>Signed peer acceptances and release state</h3>
      </div>
      {error ? (
        <p className="muted">{error}</p>
      ) : leases.length === 0 ? (
        <p className="muted">No leases yet. Negotiate one from the panel above.</p>
      ) : (
        <div className="stack">
          {leases.slice(0, 4).map((lease) => (
            <div key={lease.id} className="inner-card">
              <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
                <strong>{lease.filters.regions?.join(", ") || "any region"}</strong>
                <span className="pill">{lease.status.toLowerCase()}</span>
              </div>
              <div className="muted">
                {lease.accepted_peer_ids.length} accepted peers · ends {new Date(lease.lease_window.ends_at).toLocaleString()}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
