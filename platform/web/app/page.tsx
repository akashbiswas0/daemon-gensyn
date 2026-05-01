import Link from "next/link";

import { getNodes } from "../lib/api";

export const dynamic = "force-dynamic";

export default async function LandingPage() {
  const nodes = await getNodes().catch(() => []);
  const activeNodes = nodes.filter((node: any) => node.active);

  return (
    <>
      <section className="hero">
        <div className="hero-grid-bg" aria-hidden />
        <div className="hero-content">
          <div className="hero-badge">
            <span className="dot" /> Live · {activeNodes.length} nodes online
          </div>
          <h1 className="hero-title">
            Decentralized<br />
            execution,<br />
            <span className="hero-accent">signed end-to-end.</span>
          </h1>
          <p className="hero-sub">
            Discover regional worker peers over AXL. Lease them with signed contracts.
            Run MCP tasks. Keep every receipt in a daemon you control.
          </p>
          <div className="hero-actions">
            <Link href="/dashboard" className="btn btn-primary">
              Open Dashboard <span className="arrow">→</span>
            </Link>
            <Link href="/operators" className="btn btn-link">Become an Operator</Link>
          </div>

          <div className="hero-terminal">
            <div className="term-bar">
              <span className="term-dot red" />
              <span className="term-dot yellow" />
              <span className="term-dot green" />
              <span className="term-title">~/nodehub · operator</span>
            </div>
            <pre className="term-body">
              <code>
                <span className="term-prompt">$</span> ./Start{"\n"}
                <span className="term-mute">› launching customer-daemon on :8010</span>{"\n"}
                <span className="term-mute">› launching berlin-worker on :8110</span>{"\n"}
                <span className="term-mute">› launching tokyo-worker on :8210</span>{"\n"}
                <span className="term-ok">✓ axl transport ready</span>{"\n"}
                <span className="term-ok">✓ {nodes.length} signed advertisements imported</span>{"\n"}
                <span className="term-prompt">$</span> <span className="term-cursor">_</span>
              </code>
            </pre>
          </div>
        </div>
      </section>

      <section className="features" id="capabilities">
        <div className="features-head">
          <div className="kicker">What it does</div>
          <h2>Three primitives. One daemon.</h2>
        </div>
        <div className="features-grid">
          <article className="feature">
            <div className="feature-num">01</div>
            <h3>Discovery</h3>
            <p>Import signed peer advertisements over AXL. Filter by region, capability, and observed reputation before you commit.</p>
            <div className="feature-tag">Ed25519 signatures</div>
          </article>
          <article className="feature">
            <div className="feature-num">02</div>
            <h3>Leasing</h3>
            <p>Lock a worker with a signed lease contract. Settle on completion — no upfront escrow, no trusted broker in the path.</p>
            <div className="feature-tag">On-chain settlement</div>
          </article>
          <article className="feature">
            <div className="feature-num">03</div>
            <h3>Global Checks</h3>
            <p>Run live pings, API calls, DNS lookups, latency probes, and CDN inspection from regional workers without routing through a broker.</p>
            <div className="feature-tag">MCP-native</div>
          </article>
        </div>
      </section>

      <section className="flow" id="how">
        <div className="flow-head">
          <div className="kicker">Workflow</div>
          <h2>From intent to receipt.</h2>
          <p>Every transition is signed. Every artifact stays on your disk.</p>
        </div>
        <ol className="flow-steps">
          <li>
            <span className="step-num">1</span>
            <div>
              <h4>Discover peers</h4>
              <p>Import signed node advertisements over AXL transport.</p>
            </div>
          </li>
          <li>
            <span className="step-num">2</span>
            <div>
              <h4>Plan and negotiate</h4>
              <p>Select peers, sign the request, lock a lease.</p>
            </div>
          </li>
          <li>
            <span className="step-num">3</span>
            <div>
              <h4>Execute and verify</h4>
              <p>Workers run MCP tasks. Outputs return signed.</p>
            </div>
          </li>
          <li>
            <span className="step-num">4</span>
            <div>
              <h4>Synthesize the report</h4>
              <p>Receipts, summaries, and replay logs land locally.</p>
            </div>
          </li>
        </ol>
      </section>

      <section className="split">
        <article className="split-card dark">
          <div className="kicker light">For Operators</div>
          <h3>Onboard a worker from the website.</h3>
          <p>Connect a wallet, choose your declared region, and generate one bootstrap command to bring a local worker live.</p>
          <pre className="code-block">
            <code>
              <span className="cmt"># after cloning the repo</span>{"\n"}
              ./OnboardWorker --label "London Worker"{"\n"}
              --region london --country GB{"\n"}
              --payout-wallet 0x...
            </code>
          </pre>
          <Link href="/operators" className="btn btn-link">Open operator onboarding →</Link>
        </article>
        <article className="split-card">
          <div className="kicker">For Requesters</div>
          <h3>Submit jobs. Inspect signed results.</h3>
          <p>Target regions, attach MCP tool definitions, and watch outputs return with full provenance.</p>
          <Link href="/dashboard" className="btn btn-link">Try the dashboard →</Link>
        </article>
      </section>

      <section className="cta-final">
        <div>
          <h2>Bring up a worker in under a minute.</h2>
          <p>Connect a wallet, copy the generated <code>./OnboardWorker</code> command, and run it locally.</p>
        </div>
        <Link href="/operators" className="btn btn-primary btn-lg">Become an Operator →</Link>
      </section>

      <footer className="site-footer">
        <div>
          <strong>NodeHub</strong>
          <span>AXL-native execution</span>
        </div>
        <div className="footer-meta">
          <span>Local-first</span>
          <span>·</span>
          <span>Signed</span>
          <span>·</span>
          <span>MCP-native</span>
        </div>
      </footer>
    </>
  );
}
