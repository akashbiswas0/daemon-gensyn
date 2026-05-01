"use client";

import { useEffect, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8010";

type Lease = {
  id: string;
  status: string;
  filters?: { regions?: string[] };
  capability_name?: string;
  accepted_peer_ids?: string[];
  lease_window: { starts_at: string; ends_at: string };
};

function parseTs(s: string): number {
  return new Date(s).getTime();
}

function formatClock(ms: number) {
  const d = new Date(ms);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatDuration(ms: number) {
  if (ms < 0) return "ended";
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m`;
  const hr = Math.floor(min / 60);
  return `${hr}h ${min % 60}m`;
}

export function LeaseGantt() {
  const [leases, setLeases] = useState<Lease[]>([]);
  const [error, setError] = useState("");
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await fetch(`${API_BASE}/leases`, { cache: "no-store" });
        if (!res.ok) throw new Error(`Failed to load leases (${res.status})`);
        const payload: Lease[] = await res.json();
        if (alive) {
          setLeases(payload);
          setError("");
        }
      } catch (err) {
        if (alive) setError(err instanceof Error ? err.message : "Failed to load leases");
      }
    };
    load();
    const interval = window.setInterval(load, 5000);
    const tick = window.setInterval(() => setNow(Date.now()), 1000);
    return () => {
      alive = false;
      window.clearInterval(interval);
      window.clearInterval(tick);
    };
  }, []);

  const sorted = [...leases].sort(
    (a, b) => parseTs(a.lease_window.starts_at) - parseTs(b.lease_window.starts_at),
  );
  const last20 = sorted.slice(-20);

  let domainStart = Number.POSITIVE_INFINITY;
  let domainEnd = Number.NEGATIVE_INFINITY;
  for (const l of last20) {
    domainStart = Math.min(domainStart, parseTs(l.lease_window.starts_at));
    domainEnd = Math.max(domainEnd, parseTs(l.lease_window.ends_at));
  }
  if (last20.length === 0) {
    domainStart = now - 60 * 60 * 1000;
    domainEnd = now + 60 * 60 * 1000;
  } else {
    domainStart = Math.min(domainStart, now - 5 * 60 * 1000);
    domainEnd = Math.max(domainEnd, now + 5 * 60 * 1000);
  }
  const domainSpan = Math.max(1, domainEnd - domainStart);
  const cursorPct = ((now - domainStart) / domainSpan) * 100;

  return (
    <article className="surface-card gantt-panel">
      <div className="stack-header">
        <div>
          <div className="kicker">Lease Window Timeline</div>
          <h3>Capacity reserved over time</h3>
        </div>
        <span className="pill">{leases.length} total · showing last {last20.length}</span>
      </div>

      {error ? (
        <p className="muted">{error}</p>
      ) : last20.length === 0 ? (
        <p className="muted">No leases yet. Reserve capacity from the Leases page.</p>
      ) : (
        <div className="gantt">
          <div className="gantt-axis">
            <span className="gantt-axis-label">{formatClock(domainStart)}</span>
            <span className="gantt-axis-label center">now {formatClock(now)}</span>
            <span className="gantt-axis-label">{formatClock(domainEnd)}</span>
          </div>
          <div className="gantt-track-area">
            <div
              className="gantt-now"
              style={{ left: `${Math.max(0, Math.min(100, cursorPct))}%` }}
              aria-hidden
            />
            {last20.map((l) => {
              const start = parseTs(l.lease_window.starts_at);
              const end = parseTs(l.lease_window.ends_at);
              const left = ((start - domainStart) / domainSpan) * 100;
              const width = Math.max(0.5, ((end - start) / domainSpan) * 100);
              const isActive = l.status === "active" && now >= start && now <= end;
              const isFuture = start > now;
              return (
                <div className="gantt-row" key={l.id}>
                  <div className="gantt-row-label">
                    <strong>{l.filters?.regions?.join(", ") || "any"}</strong>
                    <span className="muted small">{l.capability_name ?? "—"}</span>
                  </div>
                  <div className="gantt-bar-track">
                    <div
                      className={`gantt-bar status-${l.status} ${isActive ? "is-active" : ""} ${isFuture ? "is-future" : ""}`}
                      style={{ left: `${left}%`, width: `${width}%` }}
                      title={`${l.status} · ${formatClock(start)} → ${formatClock(end)} (${formatDuration(end - start)})`}
                    />
                  </div>
                  <span className={`status-pill status-${l.status}`}>{l.status}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </article>
  );
}
