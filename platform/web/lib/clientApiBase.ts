// Client-only API base resolver.
//
// We can't bake a single API base into the bundle because the dashboard is
// reachable on multiple hosts (loopback for the operator running the demo,
// LAN IP for collaborators on the same network). macOS Sequoia silently
// stalls cross-origin fetches from a loopback origin to a LAN IP unless the
// browser has Local Network privacy granted, which leaves the report page
// hung on "Loading report...". Resolving the daemon URL from
// `window.location.hostname` keeps every fetch on the same host the user
// already loaded the page from, so the Local Network gate never trips.

// Always returns "/api" so client-side fetches stay same-origin and ride the
// Next.js rewrite to the daemon. This sidesteps both CORS preflight and
// macOS Sequoia's Local Network privacy gate (which silently hangs fetches
// from a loopback origin to a LAN IP). The actual daemon URL lives in the
// Next.js rewrite (`next.config.ts`), so the browser never sees it.
export function clientApiBase(): string {
  return "/api";
}
