"use client";

import { useEffect, useMemo, useState } from "react";

type EthereumProvider = {
  request: (args: { method: string; params?: unknown[] }) => Promise<unknown>;
};

declare global {
  interface Window {
    ethereum?: EthereumProvider;
  }
}

const CAPABILITY_OPTIONS = [
  { id: "browser_task", label: "0G browser tasks (primary)", required: true },
  { id: "http_check", label: "HTTP checks (secondary)", required: false },
];

const DEFAULT_BOOTSTRAP_PEER =
  process.env.NEXT_PUBLIC_OPERATOR_BOOTSTRAP_PEER ?? "";
const DEFAULT_REPO_URL =
  process.env.NEXT_PUBLIC_OPERATOR_REPO_URL ??
  "https://github.com/akashbiswas0/daemon-gensyn.git";
const REGION_OPTIONS = [
  { value: "", label: "Select region", countryCode: "" },
  { value: "london", label: "London", countryCode: "GB" },
  { value: "berlin", label: "Berlin", countryCode: "DE" },
  { value: "tokyo", label: "Tokyo", countryCode: "JP" },
  { value: "mumbai", label: "Mumbai", countryCode: "IN" },
  { value: "singapore", label: "Singapore", countryCode: "SG" },
  { value: "new-york", label: "New York", countryCode: "US" },
  { value: "san-francisco", label: "San Francisco", countryCode: "US" },
];

function countryLabel(countryCode: string) {
  switch (countryCode) {
    case "GB":
      return "United Kingdom (GB)";
    case "DE":
      return "Germany (DE)";
    case "JP":
      return "Japan (JP)";
    case "IN":
      return "India (IN)";
    case "SG":
      return "Singapore (SG)";
    case "US":
      return "United States (US)";
    default:
      return "Auto-filled from region";
  }
}

function shellQuote(value: string) {
  return `'${value.replace(/'/g, `'\"'\"'`)}'`;
}

export function OperatorOnboardingClient() {
  const [walletAddress, setWalletAddress] = useState("");
  const [walletStatus, setWalletStatus] = useState("Connect a browser wallet to bind payouts.");
  const [copyStatus, setCopyStatus] = useState("");
  const [form, setForm] = useState({
    label: "Operator Worker",
    region: "",
    countryCode: "",
    bootstrapPeer: DEFAULT_BOOTSTRAP_PEER,
    openAiEnabled: true,
    capabilities: ["browser_task", "http_check"],
  });
  const selectedRegion = REGION_OPTIONS.find((option) => option.value === form.region);

  useEffect(() => {
    const loadAccounts = async () => {
      if (!window.ethereum) {
        return;
      }
      try {
        const accounts = (await window.ethereum.request({ method: "eth_accounts" })) as string[];
        if (accounts[0]) {
          setWalletAddress(accounts[0]);
          setWalletStatus(`Connected ${accounts[0].slice(0, 6)}...${accounts[0].slice(-4)}`);
        }
      } catch {
        // Ignore passive wallet detection failures.
      }
    };
    loadAccounts();
  }, []);

  async function connectWallet() {
    if (!window.ethereum) {
      setWalletStatus("No browser wallet found. Install MetaMask or another EVM wallet.");
      return;
    }
    try {
      const accounts = (await window.ethereum.request({ method: "eth_requestAccounts" })) as string[];
      if (!accounts[0]) {
        setWalletStatus("No wallet account returned.");
        return;
      }
      setWalletAddress(accounts[0]);
      setWalletStatus(`Connected ${accounts[0].slice(0, 6)}...${accounts[0].slice(-4)}`);
    } catch (error) {
      setWalletStatus(error instanceof Error ? error.message : "Wallet connection failed.");
    }
  }

  function toggleCapability(capabilityId: string) {
    setForm((current) => {
      if (capabilityId === "browser_task") {
        return current;
      }
      const exists = current.capabilities.includes(capabilityId);
      const capabilities = exists
        ? current.capabilities.filter((item) => item !== capabilityId)
        : [...current.capabilities, capabilityId];
      return { ...current, capabilities: ["browser_task", ...capabilities.filter((item) => item !== "browser_task")] };
    });
  }

  const generatedCommand = useMemo(() => {
    if (!walletAddress || form.capabilities.length === 0 || !form.region.trim() || !form.countryCode.trim() || !form.bootstrapPeer.trim()) {
      return "";
    }
    return [
      `git clone ${DEFAULT_REPO_URL} eth-agent`,
      "cd eth-agent",
      [
        "./OnboardWorker",
        `--label ${shellQuote(form.label.trim() || "Operator Worker")}`,
        `--region ${shellQuote(form.region.trim().toLowerCase())}`,
        `--country ${shellQuote(form.countryCode.trim().toUpperCase())}`,
        `--payout-wallet ${walletAddress}`,
        `--capabilities ${shellQuote(form.capabilities.join(","))}`,
        `--seed-peer ${shellQuote(form.bootstrapPeer.trim())}`,
        `--openai-enabled ${form.openAiEnabled ? "true" : "false"}`,
      ].join(" "),
    ].join("\n");
  }, [form, walletAddress]);

  async function copyCommand() {
    if (!generatedCommand) {
      return;
    }
    await navigator.clipboard.writeText(generatedCommand);
    setCopyStatus("Copied");
    window.setTimeout(() => setCopyStatus(""), 1200);
  }

  const ready = Boolean(walletAddress && form.capabilities.length > 0 && form.region && form.countryCode && form.bootstrapPeer.trim());

  return (
    <div className="operator-layout">
      <section className="surface-card operator-hero">
          <div className="operator-copy">
          <div className="kicker">Operator Onboarding</div>
          <h1>Bring a local worker live with one command.</h1>
          <p className="muted">
            NodeHub workers run on your machine. Connect a payout wallet here, choose a declared region,
            and generate the exact repo bootstrap command to run after cloning.
          </p>
          <p className="muted">
            Prerequisites on the operator laptop: Python 3, `make`, and a browser wallet. On macOS,
            the bootstrap script can install Go automatically if Homebrew is already installed.
          </p>
          <p className="muted">
            The NodeHub worker runtime includes the browser runtime by default. <code>./OnboardWorker</code> prompts
            for your 0G testnet API key and storage private key on first run and stores them inside the same worker
            runtime config — there is no separate <code>node-nexus-agent/.env</code> contract to maintain.
          </p>
          <div className="row">
            <button type="button" className="button" onClick={connectWallet}>
              {walletAddress ? "Reconnect wallet" : "Connect wallet"}
            </button>
            <span className="pill">{walletStatus}</span>
          </div>
        </div>
        <div className="surface-card operator-sidecard">
          <div className="kicker">What Success Looks Like</div>
          <ul className="operator-checklist">
            <li>Worker daemon is healthy on the local machine.</li>
            <li>AXL peer ID is printed by the bootstrap script.</li>
            <li>The node appears as active in the requester’s `/nodes` page.</li>
            <li>Future settlements target the connected browser wallet.</li>
          </ul>
        </div>
      </section>

      <section className="operator-grid">
        <article className="surface-card stack">
          <div className="kicker">Worker Inputs</div>
          <div className="form-grid">
            <label className="field">
              <span>Node label</span>
              <input
                className="input"
                value={form.label}
                onChange={(event) => setForm((current) => ({ ...current, label: event.target.value }))}
              />
            </label>
            <label className="field">
              <span>Declared region</span>
              <select
                className="input"
                value={form.region}
                onChange={(event) => {
                  const option = REGION_OPTIONS.find((item) => item.value === event.target.value);
                  setForm((current) => ({
                    ...current,
                    region: event.target.value,
                    countryCode: option?.countryCode ?? "",
                  }));
                }}
              >
                {REGION_OPTIONS.map((option) => (
                  <option key={option.value || "blank"} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Country code</span>
              <input
                className="input"
                value={selectedRegion?.countryCode ?? ""}
                readOnly
                placeholder="Auto-filled from region"
              />
              <span className="muted">{countryLabel(selectedRegion?.countryCode ?? "")}</span>
            </label>
            <label className="field">
              <span>Bootstrap peer URI</span>
              <input
                className="input"
                value={form.bootstrapPeer}
                onChange={(event) => setForm((current) => ({ ...current, bootstrapPeer: event.target.value }))}
                placeholder="tls://192.168.1.10:9101"
              />
            </label>
          </div>

          <div className="field">
            <span>Capabilities</span>
            <div className="operator-capability-grid">
              {CAPABILITY_OPTIONS.map((capability) => {
                const selected = form.capabilities.includes(capability.id);
                return (
                  <button
                    key={capability.id}
                    type="button"
                    className={`capability-tile${selected ? " selected" : ""}`}
                    onClick={() => toggleCapability(capability.id)}
                    disabled={capability.required}
                  >
                    <strong>{capability.label}</strong>
                    <span>{capability.id}</span>
                  </button>
                );
              })}
            </div>
          </div>

          <label className="operator-toggle">
            <input
              type="checkbox"
              checked={form.openAiEnabled}
              onChange={(event) => setForm((current) => ({ ...current, openAiEnabled: event.target.checked }))}
            />
            <div>
              <strong>Enable OpenAI-assisted reasoning</strong>
              <p className="muted">If enabled, the local script will prompt for an API key on the operator machine if one is not already set.</p>
            </div>
          </label>
        </article>

        <article className="surface-card stack">
          <div className="stack-header">
            <div>
              <div className="kicker">Bootstrap Command</div>
              <h3>Clone, cd, run.</h3>
            </div>
            <button type="button" className="button button-ghost button-small" onClick={copyCommand} disabled={!ready}>
              {copyStatus || "Copy"}
            </button>
          </div>
          <p className="muted">
            The region is a declared label, not an automatic geo-verification. The worker runs on the current machine.
            For a two-laptop demo, use the bootstrap seed peer printed by the requester host.
          </p>
          <pre className="code-block operator-code">
            <code>{generatedCommand || "# Connect a wallet, choose region/country, keep at least one capability selected, and paste a bootstrap peer URI."}</code>
          </pre>
          <div className="surface-card operator-note">
            <div className="kicker">After Running It</div>
            <p className="muted">
              The script installs local dependencies, starts one worker AXL node, one daemon, and one MCP router,
              then prints the worker peer ID, payout wallet, and health endpoints.
            </p>
          </div>
        </article>
      </section>
    </div>
  );
}
