"use client";

import { useState } from "react";

type Props = {
  value: string;
  display?: string;
  ariaLabel?: string;
};

export function CopyableId({ value, display, ariaLabel = "Copy" }: Props) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      const textarea = document.createElement("textarea");
      textarea.value = value;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      try {
        document.execCommand("copy");
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1400);
      } catch {
        // give up silently
      }
      document.body.removeChild(textarea);
    }
  };

  const shown = display ?? `${value.slice(0, 14)}…`;

  return (
    <span className="copy-cell mono" title={value}>
      <span className="copy-text">{shown}</span>
      <button
        type="button"
        className="copy-btn"
        onClick={handleCopy}
        aria-label={copied ? "Copied" : ariaLabel}
      >
        {copied ? (
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="20 6 9 17 4 12" />
          </svg>
        ) : (
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="9" y="9" width="13" height="13" rx="2" />
            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
          </svg>
        )}
      </button>
    </span>
  );
}
