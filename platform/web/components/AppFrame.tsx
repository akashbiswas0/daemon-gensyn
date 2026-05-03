"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ReactNode } from "react";

import { IdentityBadge } from "./IdentityBadge";

type Identity = {
  wallet_address: string;
  peer_id: string;
  label: string;
  region: string;
  country_code: string;
  worker_enabled: boolean;
  payment_mode: string;
};

const NAV_ITEMS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/jobs", label: "Jobs" },
  { href: "/activities", label: "Activities" },
  { href: "/nodes", label: "Nodes" },
  { href: "/ledger", label: "Ledger" },
];

const PAGE_TITLES: Array<[RegExp, { title: string; subtitle: string }]> = [
  [/^\/dashboard$/, { title: "Operator Overview", subtitle: "Active browser workers and execution." }],
  [/^\/jobs$/, { title: "Jobs", subtitle: "Run signed browser tasks." }],
  [/^\/jobs\/[^/]+$/, { title: "Job Report", subtitle: "Signed receipts and outcomes." }],
  [/^\/activities$/, { title: "Activities", subtitle: "Recent jobs and signed receipts." }],
  [/^\/nodes$/, { title: "Discovered Nodes", subtitle: "Active browser workers only." }],
  [/^\/ledger$/, { title: "Trust Ledger", subtitle: "Signed attestations only." }],
  [/^\/earnings$/, { title: "Trust Ledger", subtitle: "Signed attestations only." }],
];

function pageMeta(pathname: string) {
  for (const [pattern, meta] of PAGE_TITLES) {
    if (pattern.test(pathname)) {
      return meta;
    }
  }
  return { title: "NodeHub Console", subtitle: "Local AXL control surface." };
}

export function AppFrame({ children, initialIdentity = null }: { children: ReactNode; initialIdentity?: Identity | null }) {
  const pathname = usePathname();
  const isMarketing = pathname === "/" || pathname === "/operators";
  const meta = pageMeta(pathname);

  if (isMarketing) {
    return (
      <div className="marketing-shell">
        <header className="marketing-topbar">
          <Link href="/" className="brand-lockup">
            <span className="brand-mark">NodeHub</span>
            <span className="brand-submark">AXL-native execution</span>
          </Link>
          <nav className="marketing-nav">
            <Link href="/#capabilities">Capabilities</Link>
            <Link href="/#how">Workflow</Link>
            <Link href="/operators">Operators</Link>
            <Link href="/dashboard" className="button button-ghost">Open Console</Link>
          </nav>
        </header>
        <main className="marketing-main">{children}</main>
      </div>
    );
  }

  return (
    <div className="console-shell">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <h1>Operator Console</h1>
          <p>Local AXL runtime</p>
        </div>
        <nav className="sidebar-nav">
          {NAV_ITEMS.map((item) => {
            const active = pathname === item.href || (item.href !== "/dashboard" && pathname.startsWith(`${item.href}/`));
            return (
              <Link key={item.href} href={item.href} className={`sidebar-link${active ? " active" : ""}`}>
                <span className="sidebar-icon" />
                <span>{item.label}</span>
              </Link>
            );
          })}
        </nav>
      </aside>
      <div className="console-main">
        <header className="console-topbar">
          <div>
            <h2>{meta.title}</h2>
            <p>{meta.subtitle}</p>
          </div>
          <div className="console-actions">
            <IdentityBadge initialIdentity={initialIdentity} />
          </div>
        </header>
        <main className="console-content">{children}</main>
      </div>
    </div>
  );
}
