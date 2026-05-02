import "./globals.css";
import { IBM_Plex_Mono, Space_Grotesk } from "next/font/google";
import { ReactNode } from "react";
import { AppFrame } from "../components/AppFrame";
import { getIdentity } from "../lib/api";

const display = Space_Grotesk({
  subsets: ["latin"],
  variable: "--font-display",
});

const mono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-mono",
});

export const metadata = {
  title: "NodeHub",
  description: "Local-first dashboard for decentralized AXL-backed browser-task coordination."
};

export const dynamic = "force-dynamic";

export default async function RootLayout({ children }: { children: ReactNode }) {
  const identity = await getIdentity().catch(() => null);
  return (
    <html lang="en" className={`${display.variable} ${mono.variable}`}>
      <body>
        <AppFrame initialIdentity={identity}>{children}</AppFrame>
      </body>
    </html>
  );
}
