"use client";

import { useEffect, useRef, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8010";

type NodeRecord = {
  region?: string;
  active?: boolean;
  last_seen_at?: string;
};

type RegionTally = {
  region: string;
  active: number;
  total: number;
  pulse: boolean;
};

function tallyNodes(nodes: NodeRecord[], previous: Map<string, { active: number; total: number }>) {
  const tally = new Map<string, { active: number; total: number }>();
  for (const n of nodes) {
    const region = (n.region || "unknown").toLowerCase();
    const prev = tally.get(region) ?? { active: 0, total: 0 };
    tally.set(region, {
      active: prev.active + (n.active ? 1 : 0),
      total: prev.total + 1,
    });
  }

  const result: RegionTally[] = Array.from(tally.entries())
    .map(([region, counts]) => {
      const prev = previous.get(region);
      const pulse = !prev || prev.active !== counts.active || prev.total !== counts.total;
      return { region, active: counts.active, total: counts.total, pulse };
    })
    .sort((a, b) => b.active - a.active);

  return { tally, result };
}

export function RegionHeatmap({
  initialNodes = [],
  className = "",
}: {
  initialNodes?: NodeRecord[];
  className?: string;
}) {
  const initialPrevious = new Map<string, { active: number; total: number }>();
  const initialTally = tallyNodes(initialNodes, initialPrevious);
  const [tallies, setTallies] = useState<RegionTally[]>(initialTally.result.map((item) => ({ ...item, pulse: false })));
  const [error, setError] = useState("");
  const previousRef = useRef<Map<string, { active: number; total: number }>>(initialTally.tally);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await fetch(`${API_BASE}/nodes`, { cache: "no-store" });
        if (!res.ok) throw new Error(`Failed to load nodes (${res.status})`);
        const nodes: NodeRecord[] = await res.json();
        if (!alive) return;

        const previous = previousRef.current;
        const next = tallyNodes(nodes, previous);
        previousRef.current = next.tally;
        setTallies(next.result);
        setError("");

        if (next.result.some((r) => r.pulse)) {
          window.setTimeout(() => {
            if (!alive) return;
            setTallies((curr) => curr.map((r) => ({ ...r, pulse: false })));
          }, 900);
        }
      } catch (err) {
        if (alive) setError(err instanceof Error ? err.message : "Failed to load nodes");
      }
    };

    load();
    const interval = window.setInterval(load, 3000);
    return () => {
      alive = false;
      window.clearInterval(interval);
    };
  }, []);

  const totalActive = tallies.reduce((sum, t) => sum + t.active, 0);

  return (
    <article className={`surface-card heatmap-panel ${className}`.trim()}>
      <div className="kicker">Network</div>
      <h3>Regions live</h3>
      {error ? (
        <p className="muted">{error}</p>
      ) : tallies.length === 0 ? (
        <p className="muted">No peers discovered yet.</p>
      ) : (
        <>
          <div className="heatmap-summary muted">
            {totalActive} active across {tallies.length} {tallies.length === 1 ? "region" : "regions"}
          </div>
          <div className="heatmap-grid">
            {tallies.map((t) => {
              const ratio = t.total > 0 ? t.active / t.total : 0;
              return (
                <div
                  key={t.region}
                  className={`heatmap-cell ${t.pulse ? "pulse" : ""}`}
                  style={{
                    background: `linear-gradient(135deg, rgba(15, 118, 110, ${0.08 + ratio * 0.32}), rgba(194, 65, 12, ${ratio * 0.18}))`,
                  }}
                >
                  <span className="heatmap-region">{t.region}</span>
                  <strong className="heatmap-count">{t.active}</strong>
                  <span className="muted small">of {t.total}</span>
                </div>
              );
            })}
          </div>
        </>
      )}
    </article>
  );
}
