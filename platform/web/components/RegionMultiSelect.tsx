"use client";

import { useEffect, useMemo, useRef, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8010";

type RegionOption = {
  region: string;
  countryCode: string;
};

function flagEmoji(countryCode: string): string {
  if (!countryCode || countryCode.length !== 2) return "🌐";
  const upper = countryCode.toUpperCase();
  const codepoints = [0, 1].map((i) => 0x1f1e6 + (upper.charCodeAt(i) - 0x41));
  if (codepoints.some((c) => c < 0x1f1e6 || c > 0x1f1ff)) return "🌐";
  return String.fromCodePoint(...codepoints);
}

type Props = {
  value: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  options?: RegionOption[];
};

export function RegionMultiSelect({ value, onChange, placeholder = "Select regions", options: initialOptions = [] }: Props) {
  const [options, setOptions] = useState<RegionOption[]>(initialOptions);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (initialOptions.length > 0) {
      setOptions(initialOptions);
      return;
    }
    let alive = true;
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/nodes`, { cache: "no-store" });
        if (!res.ok) return;
        const nodes = await res.json();
        if (!alive) return;
        const seen = new Map<string, string>();
        for (const n of nodes) {
          const region = String(n.region ?? "").toLowerCase();
          const cc = String(n.country_code ?? "").toUpperCase();
          if (!region) continue;
          if (!seen.has(region)) seen.set(region, cc);
        }
        setOptions(
          Array.from(seen.entries())
            .map(([region, countryCode]) => ({ region, countryCode }))
            .sort((a, b) => a.region.localeCompare(b.region)),
        );
      } catch {
        // leave options empty; the user can still type-fall-back via free-text not available here
      }
    })();
    return () => {
      alive = false;
    };
  }, [initialOptions]);

  useEffect(() => {
    if (!open) return;
    const handler = (event: MouseEvent) => {
      if (!containerRef.current) return;
      if (!containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const valueSet = useMemo(() => new Set(value.map((v) => v.toLowerCase())), [value]);

  const toggle = (region: string) => {
    const lower = region.toLowerCase();
    if (valueSet.has(lower)) {
      onChange(value.filter((v) => v.toLowerCase() !== lower));
    } else {
      onChange([...value, lower]);
    }
  };

  const remove = (region: string) => {
    onChange(value.filter((v) => v.toLowerCase() !== region.toLowerCase()));
  };

  const addFromDraft = () => {
    const trimmed = draft.trim().toLowerCase();
    if (!trimmed) return;
    if (!valueSet.has(trimmed)) {
      onChange([...value, trimmed]);
    }
    setDraft("");
  };

  const optionByRegion = useMemo(() => {
    const m = new Map<string, RegionOption>();
    for (const opt of options) m.set(opt.region.toLowerCase(), opt);
    return m;
  }, [options]);

  return (
    <div className={`region-select ${open ? "is-open" : ""}`} ref={containerRef}>
      <button
        type="button"
        className="region-trigger"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        {value.length === 0 ? (
          <span className="region-placeholder">{placeholder}</span>
        ) : (
          <div className="region-chips">
            {value.map((region) => {
              const opt = optionByRegion.get(region.toLowerCase());
              return (
                <span key={region} className="region-chip">
                  <span className="region-flag">{opt ? flagEmoji(opt.countryCode) : "🌐"}</span>
                  <span>{region}</span>
                  <span
                    role="button"
                    tabIndex={0}
                    aria-label={`Remove ${region}`}
                    className="region-chip-x"
                    onClick={(e) => {
                      e.stopPropagation();
                      remove(region);
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        e.stopPropagation();
                        remove(region);
                      }
                    }}
                  >
                    ×
                  </span>
                </span>
              );
            })}
          </div>
        )}
        <span className="region-caret" aria-hidden>
          ▾
        </span>
      </button>
      {open && (
        <ul className="region-menu" role="listbox">
          <li
            className="region-option"
            role="presentation"
            onClick={(e) => e.stopPropagation()}
            style={{ display: "flex", gap: 6, alignItems: "center", padding: 8 }}
          >
            <input
              className="input"
              placeholder="Type a region (e.g. tokyo)"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  addFromDraft();
                }
              }}
              style={{ flex: 1, padding: "6px 8px" }}
            />
            <button
              type="button"
              className="button button-ghost button-small"
              onClick={addFromDraft}
              disabled={!draft.trim()}
            >
              Add
            </button>
          </li>
          {options.length === 0 ? (
            <li className="region-empty">No regions discovered yet — type one above.</li>
          ) : (
            options.map((opt) => {
              const selected = valueSet.has(opt.region);
              return (
                <li
                  key={opt.region}
                  className={`region-option ${selected ? "is-selected" : ""}`}
                  role="option"
                  aria-selected={selected}
                  onClick={() => toggle(opt.region)}
                >
                  <span className="region-flag">{flagEmoji(opt.countryCode)}</span>
                  <span className="region-name">{opt.region}</span>
                  <span className="region-cc muted">{opt.countryCode || "—"}</span>
                  {selected && <span className="region-check">✓</span>}
                </li>
              );
            })
          )}
        </ul>
      )}
    </div>
  );
}
