"use client";

import { useEffect, useRef, useState } from "react";

type DaemonProbe = {
  name: string;
  url: string;
};

type DaemonState = {
  ok: boolean | null;
  latencyMs: number | null;
  checkedAt: number | null;
  consecutiveFailures: number;
};

const DEFAULT_DAEMONS: DaemonProbe[] = [
  { name: "Customer", url: "http://127.0.0.1:8010" },
  { name: "Berlin Worker", url: "http://127.0.0.1:8110" },
  { name: "Tokyo Worker", url: "http://127.0.0.1:8210" },
];

function formatAge(ts: number | null) {
  if (ts == null) return "—";
  const diff = Math.max(0, Date.now() - ts);
  const sec = Math.floor(diff / 1000);
  if (sec < 2) return "just now";
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  return `${min}m ago`;
}

export function DaemonHealthPanel({ daemons = DEFAULT_DAEMONS }: { daemons?: DaemonProbe[] }) {
  const [states, setStates] = useState<Record<string, DaemonState>>(() =>
    Object.fromEntries(
      daemons.map((d) => [d.url, { ok: null, latencyMs: null, checkedAt: null, consecutiveFailures: 0 }]),
    ),
  );
  const [, forceTick] = useState(0);
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    const probe = async () => {
      const next = await Promise.all(
        daemons.map(async (d) => {
          const start = performance.now();
          try {
            const ctrl = new AbortController();
            const timeout = window.setTimeout(() => ctrl.abort(), 2500);
            const res = await fetch(`${d.url}/health`, { cache: "no-store", signal: ctrl.signal });
            window.clearTimeout(timeout);
            const ok = res.ok;
            return [d.url, { ok, latencyMs: performance.now() - start, checkedAt: Date.now() }] as const;
          } catch {
            return [d.url, { ok: false, latencyMs: null, checkedAt: Date.now() }] as const;
          }
        }),
      );
      if (!aliveRef.current) return;
      setStates((prev) => {
        const updated = { ...prev };
        for (const [url, sample] of next) {
          const old = prev[url] ?? { consecutiveFailures: 0 };
          updated[url] = {
            ok: sample.ok,
            latencyMs: sample.latencyMs,
            checkedAt: sample.checkedAt,
            consecutiveFailures: sample.ok ? 0 : (old.consecutiveFailures ?? 0) + 1,
          };
        }
        return updated;
      });
    };
    probe();
    const interval = window.setInterval(probe, 2500);
    const tick = window.setInterval(() => forceTick((n) => n + 1), 1000);
    return () => {
      aliveRef.current = false;
      window.clearInterval(interval);
      window.clearInterval(tick);
    };
  }, [daemons]);

  const allOk = daemons.every((d) => states[d.url]?.ok);
  const anyChecked = daemons.some((d) => states[d.url]?.checkedAt != null);

  return (
    <article className="surface-card health-panel">
      <div className="kicker">Liveness</div>
      <h3>Daemon health</h3>
      <div className="health-summary">
        <span className={`health-dot ${anyChecked ? (allOk ? "ok" : "fail") : "pending"}`} />
        <span className="muted">
          {!anyChecked ? "probing..." : allOk ? "all daemons responding" : "degraded"}
        </span>
      </div>
      <ul className="health-list">
        {daemons.map((d) => {
          const s = states[d.url];
          const status = s?.ok == null ? "pending" : s.ok ? "ok" : "fail";
          return (
            <li key={d.url} className="health-row">
              <span className={`health-dot ${status}`} />
              <div className="health-label">
                <strong>{d.name}</strong>
                <span className="muted">{d.url.replace("http://", "")}</span>
              </div>
              <div className="health-meta">
                {s?.ok && s.latencyMs != null ? (
                  <span className="mono">{Math.round(s.latencyMs)}ms</span>
                ) : s?.ok === false ? (
                  <span className="mono fail-text">down</span>
                ) : (
                  <span className="mono muted">—</span>
                )}
                <span className="muted small">{formatAge(s?.checkedAt ?? null)}</span>
              </div>
            </li>
          );
        })}
      </ul>
    </article>
  );
}
