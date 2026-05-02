"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8010";

type Identity = {
  wallet_address: string;
  peer_id: string;
  label: string;
  region: string;
  country_code: string;
  worker_enabled: boolean;
  payment_mode: string;
};

function formatIdentity(identity: Identity) {
  return `${identity.region.toUpperCase()} · ${identity.wallet_address.slice(0, 6)}...${identity.wallet_address.slice(-4)}`;
}

export function IdentityBadge({ initialIdentity = null }: { initialIdentity?: Identity | null }) {
  const router = useRouter();
  const [identity, setIdentity] = useState<Identity | null>(initialIdentity);
  const [status, setStatus] = useState(
    initialIdentity ? formatIdentity(initialIdentity) : "Daemon unavailable",
  );
  const [discovering, setDiscovering] = useState(false);

  useEffect(() => {
    const load = async () => {
      try {
        const response = await fetch(`${API_BASE}/identity`, { cache: "no-store" });
        if (!response.ok) {
          throw new Error(`Daemon unavailable (${response.status})`);
        }
        const payload = await response.json();
        setIdentity(payload);
        setStatus(formatIdentity(payload));
      } catch (error) {
        if (!initialIdentity) {
          setStatus(error instanceof Error ? error.message : "Failed to reach local daemon");
        }
      }
    };
    load();
  }, [initialIdentity]);

  async function discover() {
    setDiscovering(true);
    try {
      const response = await fetch(`${API_BASE}/discover`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ peer_ids: [] }),
      });
      if (!response.ok) {
        throw new Error(`Discovery failed (${response.status})`);
      }
      const payload = await response.json();
      setStatus(`Discovered ${payload.length} nodes.`);
      router.refresh();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Discovery failed");
    } finally {
      setDiscovering(false);
    }
  }

  return (
    <div className="identity-badge">
      <div className="identity-text">
        <strong>{identity ? identity.label : "Local daemon"}</strong>
        <span>{identity ? formatIdentity(identity) : status}</span>
      </div>
      <button className="button button-ghost button-small" onClick={discover} disabled={discovering}>
        {discovering ? "Syncing..." : "Discover"}
      </button>
    </div>
  );
}
