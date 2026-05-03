import type { NextConfig } from "next";

// Proxy daemon traffic through the Next.js dev server so the browser only
// ever fetches from the same origin it loaded the page from. Avoids macOS
// Sequoia Local Network privacy stalls (where a loopback origin fetching a
// LAN-IP host hangs forever) and removes the CORS preflight entirely.
const DAEMON_TARGET =
  process.env.NEXT_PROXY_DAEMON_URL ?? "http://127.0.0.1:8010";

const nextConfig: NextConfig = {
  typedRoutes: false,
  // Next.js 16 blocks /_next/webpack-hmr (and other dev assets) from any
  // origin not in this list, which leaves client components stuck in a
  // half-hydrated state. We expose the dashboard on both loopback and the
  // LAN IP, so both must be allowed.
  allowedDevOrigins: [
    "127.0.0.1",
    "localhost",
    process.env.NEXT_PUBLIC_OPERATOR_BOOTSTRAP_PEER?.match(/\/\/([^:/]+)/)?.[1] ?? "",
    process.env.DASHBOARD_LAN_IP ?? "",
  ].filter(Boolean),
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${DAEMON_TARGET}/:path*` }];
  },
};

export default nextConfig;
