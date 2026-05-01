const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8010";

async function fetchJson(path: string, init?: RequestInit) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    }
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || `Request failed: ${response.status}`);
  }
  return response.json();
}

export async function getNodes() {
  return fetchJson("/nodes");
}

export async function getIdentity() {
  return fetchJson("/identity");
}

export async function discoverNodes(peer_ids: string[] = []) {
  return fetchJson("/discover", {
    method: "POST",
    body: JSON.stringify({ peer_ids }),
  });
}

export async function getJobs() {
  return fetchJson("/jobs");
}

export async function getJobReport(jobId: string) {
  return fetchJson(`/reports/jobs/${jobId}`);
}

export async function getLeases() {
  return fetchJson("/leases");
}

export async function getAttestations() {
  return fetchJson("/attestations");
}

export async function getSettlements() {
  return fetchJson("/settlements");
}

